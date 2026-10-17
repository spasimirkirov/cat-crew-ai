//! CatCrewAI desktop shell.
//!
//! What lives here, and nothing more:
//!   * the native window. It is declared hidden with the app background colour
//!     (`tauri.conf.json`), so the OS never paints an empty frame. The React app reveals it
//!     after its first painted frame (`src/lib/window.ts`); a failsafe timer below shows it
//!     anyway if the UI never does, so a broken front end cannot leave an invisible app.
//!   * the Python backend (`python -m backend`). If one is already answering on the loopback
//!     port (a dev running it by hand) it is used as-is and left alone. Otherwise it is started
//!     here, and stopped on exit: `POST /api/shutdown` first, then `kill` as a last resort.
//!
//!   * the system tray and the window lifecycle. The "X" button hides the window to the tray
//!     instead of quitting; the tray menu ("Show CatCrewAI" / "Quit CatCrewAI"), a left click on
//!     the tray icon, and the UI's Power Off button (`quit_app`) are the ways to show it again
//!     or to really quit. Quitting always stops the backend this shell started.
//!
//! Every business rule stays in the backend; this file never touches SQLite, files or agents.
//!
//! Environment:
//!   CATCREW_BACKEND=external   never start or stop a backend
//!   CATCREW_PORT               backend port (default 8765; the CSP allows 8765 only)
//!   CATCREW_PYTHON             interpreter to run the backend with
//!   CATCREW_REPO               folder that contains the `backend/` package

use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use serde::Serialize;
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_notification::NotificationExt;

const HOST: &str = "127.0.0.1";
const DEFAULT_PORT: u16 = 8765;
const READY_LINE: &str = "CATCREW_LISTENING";
const READY_TIMEOUT: Duration = Duration::from_secs(25);
const SHOW_FAILSAFE: Duration = Duration::from_millis(4000);
const STOP_GRACE: Duration = Duration::from_secs(6);

const MAIN_WINDOW: &str = "main";
const TRAY_ID: &str = "main"; // must match `app.trayIcon.id` in tauri.conf.json
const MENU_SHOW: &str = "show";
const MENU_QUIT: &str = "quit";
const BACKGROUND_NOTICE: &str = "CatCrewAI is running in the background.";

/// The backend process this shell started (None when it attached to one that was already running).
struct OwnedBackend(Mutex<Option<Child>>);

/// How many of the engine's last output lines are kept, to explain a failed start.
const TAIL_LINES: usize = 14;
/// A line longer than this is cut (a stray blob must not fill the UI).
const TAIL_LINE_MAX: usize = 300;

/// What the UI is told about the engine (`backend_status`). The UI also asks the backend over HTTP; this
/// adds what only the shell knows: that the engine never started, or died, and why.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "state", rename_all = "lowercase")]
enum BackendStatus {
    /// Spawned, or about to be, and not answering yet: a normal state for the first seconds.
    Starting,
    /// Answering (started here, or attached to one that was already running).
    Ready,
    /// `CATCREW_BACKEND=external`: not this shell's business; the UI just keeps asking over HTTP.
    External,
    /// It will not come up. `detail` carries the reason and the tail of the engine's own output.
    Failed { detail: String },
}

struct BackendInfo {
    status: Mutex<BackendStatus>,
    tail: Arc<Mutex<VecDeque<String>>>,
}

impl BackendInfo {
    fn new() -> Self {
        Self { status: Mutex::new(BackendStatus::Starting), tail: Arc::new(Mutex::new(VecDeque::new())) }
    }
}

/// Keep the last `TAIL_LINES` non-empty lines of the engine's output.
fn push_tail(tail: &Mutex<VecDeque<String>>, line: &str) {
    let line = line.trim_end();
    if line.trim().is_empty() {
        return;
    }
    let mut q = tail.lock().unwrap();
    if q.len() == TAIL_LINES {
        q.pop_front();
    }
    q.push_back(line.chars().take(TAIL_LINE_MAX).collect());
}

fn tail_text(tail: &Mutex<VecDeque<String>>) -> String {
    tail.lock().unwrap().iter().cloned().collect::<Vec<_>>().join("\n")
}

fn failure_detail(reason: &str, tail: &str) -> String {
    if tail.trim().is_empty() {
        reason.to_string()
    } else {
        format!("{reason}\n\n{tail}")
    }
}

fn set_status(app: &AppHandle, status: BackendStatus) {
    *app.state::<BackendInfo>().status.lock().unwrap() = status;
}

/// Asked by the UI while it starts up: is the engine coming, up, or never going to?
#[tauri::command]
fn backend_status(info: tauri::State<BackendInfo>) -> BackendStatus {
    info.status.lock().unwrap().clone()
}

/// Window-lifecycle flags shared by the tray, the close handler and `quit_app`.
#[derive(Default)]
struct Lifecycle {
    /// Set once a real quit has begun: closing must no longer be turned into "hide".
    quitting: AtomicBool,
    /// The "running in the background" notice is shown once per run, not on every hide.
    told_background: AtomicBool,
}

impl Lifecycle {
    /// True only for the first caller: the one that has to start the shutdown.
    fn begin_quit(&self) -> bool {
        !self.quitting.swap(true, Ordering::SeqCst)
    }
    fn is_quitting(&self) -> bool {
        self.quitting.load(Ordering::SeqCst)
    }
    /// True only the first time it is asked: the one that shows the notice.
    fn first_background_hide(&self) -> bool {
        !self.told_background.swap(true, Ordering::SeqCst)
    }
}

fn port() -> u16 {
    std::env::var("CATCREW_PORT").ok().and_then(|v| v.parse().ok()).unwrap_or(DEFAULT_PORT)
}

/// One tiny HTTP/1.1 exchange over loopback. Returns (status, body). No Origin header is sent,
/// which the backend's local-only middleware accepts (only browsers send one).
fn http(method: &str, path: &str, port: u16) -> Option<(u16, String)> {
    let addr: SocketAddr = format!("{HOST}:{port}").parse().ok()?;
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_millis(600)).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(3))).ok()?;
    stream.set_write_timeout(Some(Duration::from_secs(3))).ok()?;
    let req = format!("{method} {path} HTTP/1.1\r\nHost: {HOST}:{port}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
    stream.write_all(req.as_bytes()).ok()?;
    let mut raw = String::new();
    stream.read_to_string(&mut raw).ok()?;
    let status = raw.lines().next()?.split_whitespace().nth(1)?.parse().ok()?;
    let body = raw.split("\r\n\r\n").nth(1).unwrap_or("").to_string();
    Some((status, body))
}

fn backend_healthy(port: u16) -> bool {
    matches!(http("GET", "/api/health", port), Some((200, body)) if body.contains("\"ok\""))
}

fn repo_root() -> PathBuf {
    if let Ok(p) = std::env::var("CATCREW_REPO") {
        return PathBuf::from(p);
    }
    // frontend/src-tauri -> repository root (correct for `tauri dev` and locally built binaries)
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn python_candidates() -> Vec<String> {
    if let Ok(p) = std::env::var("CATCREW_PYTHON") {
        return vec![p];
    }
    if cfg!(windows) {
        vec!["python".into(), "py".into()]
    } else {
        vec!["python3".into(), "python".into()]
    }
}

fn spawn_backend(root: &Path, port: u16) -> Result<Child, String> {
    let mut last = String::from("no Python interpreter found");
    for py in python_candidates() {
        let mut cmd = Command::new(&py);
        cmd.args(["-m", "backend", "--port", &port.to_string()])
            .current_dir(root)
            .env("PYTHONUNBUFFERED", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console flashing up
        }
        match cmd.spawn() {
            Ok(child) => return Ok(child),
            Err(e) => last = format!("{py}: {e}"),
        }
    }
    Err(last)
}

/// Forward the engine's stdout and stderr to ours, keep their tail, and signal when it says it is listening.
fn watch_output(child: &mut Child, tail: Arc<Mutex<VecDeque<String>>>) -> mpsc::Receiver<()> {
    let (tx, rx) = mpsc::channel();
    if let Some(out) = child.stdout.take() {
        let tail = tail.clone();
        thread::spawn(move || {
            for line in BufReader::new(out).lines().map_while(Result::ok) {
                if line.starts_with(READY_LINE) {
                    let _ = tx.send(());
                }
                println!("[backend] {line}");
                push_tail(&tail, &line);
            }
        });
    }
    if let Some(err) = child.stderr.take() {
        thread::spawn(move || {
            for line in BufReader::new(err).lines().map_while(Result::ok) {
                eprintln!("[backend] {line}");
                push_tail(&tail, &line);
            }
        });
    }
    rx
}

#[derive(Debug, PartialEq, Eq)]
enum ChildState {
    Running,
    /// The process has ended (its exit status, as text).
    Exited(String),
    /// Nobody owns it any more: the app is quitting.
    Gone,
}

#[derive(Debug, PartialEq, Eq)]
enum Wait {
    Ready,
    Exited(String),
    TimedOut,
    Quit,
}

/// Wait for the engine to come up: its "listening" line, or (as a fallback, once a second) a healthy
/// answer. Stops early when the process dies or the app quits, so a broken start is reported in a
/// moment instead of after the full timeout.
fn wait_until_ready(
    ready: &mpsc::Receiver<()>,
    mut child: impl FnMut() -> ChildState,
    mut healthy: impl FnMut() -> bool,
    timeout: Duration,
    tick: Duration,
) -> Wait {
    let started = Instant::now();
    let mut last_health = Instant::now();
    loop {
        match ready.recv_timeout(tick) {
            Ok(()) => return Wait::Ready,
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            // the output pipes closed (the process is ending): do not spin, the checks below decide
            Err(mpsc::RecvTimeoutError::Disconnected) => thread::sleep(tick),
        }
        match child() {
            ChildState::Exited(status) => return Wait::Exited(status),
            ChildState::Gone => return Wait::Quit,
            ChildState::Running => {}
        }
        if last_health.elapsed() >= Duration::from_secs(1) {
            last_health = Instant::now();
            if healthy() {
                return Wait::Ready;
            }
        }
        if started.elapsed() >= timeout {
            return if healthy() { Wait::Ready } else { Wait::TimedOut };
        }
    }
}

fn child_state(app: &AppHandle) -> ChildState {
    match app.state::<OwnedBackend>().0.lock().unwrap().as_mut() {
        None => ChildState::Gone,
        Some(child) => match child.try_wait() {
            Ok(Some(status)) => ChildState::Exited(status.to_string()),
            _ => ChildState::Running,
        },
    }
}

fn start_backend(app: &tauri::AppHandle) {
    let port = port();
    if std::env::var("CATCREW_BACKEND").map(|v| v == "external").unwrap_or(false) {
        eprintln!("[catcrewai] CATCREW_BACKEND=external: not managing a backend");
        set_status(app, BackendStatus::External);
        return;
    }
    if backend_healthy(port) {
        eprintln!("[catcrewai] backend already running on {HOST}:{port}; attaching");
        set_status(app, BackendStatus::Ready);
        return;
    }
    let root = repo_root();
    let mut child = match spawn_backend(&root, port) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[catcrewai] could not start the backend ({e}). Start it with `python -m backend`.");
            let reason = format!("Could not start Python ({e}). Install Python 3, or set CATCREW_PYTHON to your interpreter.");
            set_status(app, BackendStatus::Failed { detail: reason });
            return;
        }
    };
    let tail = app.state::<BackendInfo>().tail.clone();
    let ready = watch_output(&mut child, tail.clone());
    // Store the child before waiting, so quitting mid-start still stops it.
    *app.state::<OwnedBackend>().0.lock().unwrap() = Some(child);
    let handle = app.clone();
    thread::spawn(move || {
        let started = Instant::now();
        let outcome = wait_until_ready(
            &ready,
            || child_state(&handle),
            || backend_healthy(port),
            READY_TIMEOUT,
            Duration::from_millis(200),
        );
        match outcome {
            Wait::Ready => set_status(&handle, BackendStatus::Ready),
            Wait::Quit => {}
            Wait::Exited(status) => {
                let reason = format!("The engine stopped while starting ({status}).");
                eprintln!("[catcrewai] {reason}");
                set_status(&handle, BackendStatus::Failed { detail: failure_detail(&reason, &tail_text(&tail)) });
            }
            Wait::TimedOut => {
                let reason = format!("The engine did not report ready within {} seconds.", started.elapsed().as_secs());
                eprintln!("[catcrewai] {reason}");
                set_status(&handle, BackendStatus::Failed { detail: failure_detail(&reason, &tail_text(&tail)) });
            }
        }
    });
}

fn stop_backend(app: &tauri::AppHandle) {
    let Some(mut child) = app.state::<OwnedBackend>().0.lock().unwrap().take() else {
        return; // attached to somebody else's backend: leave it running
    };
    let _ = http("POST", "/api/shutdown", port());
    let deadline = Instant::now() + STOP_GRACE;
    while Instant::now() < deadline {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    let _ = child.wait();
}

// --------------------------------------------------------------------------- //
// Window lifecycle: tray, close-to-tray, quit
// --------------------------------------------------------------------------- //

#[derive(Debug, PartialEq, Eq)]
enum TrayToggle {
    Show,
    Hide,
}

/// A left click on the tray icon: hide the window when it is on screen, otherwise bring it back.
/// (Focus is deliberately not consulted: clicking the tray takes focus away from the window on
/// most platforms, so "focused" would never be true at the moment of the click.)
fn tray_toggle(visible: bool, minimized: bool) -> TrayToggle {
    if visible && !minimized {
        TrayToggle::Hide
    } else {
        TrayToggle::Show
    }
}

fn show_main(app: &AppHandle) {
    if let Some(win) = app.get_webview_window(MAIN_WINDOW) {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
}

fn hide_main(app: &AppHandle) {
    if let Some(win) = app.get_webview_window(MAIN_WINDOW) {
        let _ = win.hide();
    }
}

fn toggle_main(app: &AppHandle) {
    let Some(win) = app.get_webview_window(MAIN_WINDOW) else { return };
    let action = tray_toggle(win.is_visible().unwrap_or(false), win.is_minimized().unwrap_or(false));
    match action {
        TrayToggle::Hide => hide_main(app),
        TrayToggle::Show => show_main(app),
    }
}

fn notify_background(app: &AppHandle) {
    if !app.state::<Lifecycle>().first_background_hide() {
        return;
    }
    let _ = app.notification().builder().title("CatCrewAI").body(BACKGROUND_NOTICE).show();
}

/// Really quit: hide the window at once (so it feels instant), stop the backend this shell
/// started (graceful `POST /api/shutdown`, then kill), and exit. Idempotent, and never blocks
/// the caller: `stop_backend` can take a few seconds and this may be running on the UI thread.
fn quit(app: &AppHandle) {
    if !app.state::<Lifecycle>().begin_quit() {
        return;
    }
    hide_main(app);
    let handle = app.clone();
    thread::spawn(move || {
        stop_backend(&handle);
        handle.exit(0);
    });
}

/// Called by the UI's Power Off button (`invoke("quit_app")`).
#[tauri::command]
fn quit_app(app_handle: AppHandle) {
    quit(&app_handle);
}

/// The tray icon comes from `app.trayIcon` in tauri.conf.json (icon + tooltip); this attaches the
/// menu and the click behaviour. If the config did not create it, it is built here.
fn setup_tray(app: &tauri::App) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, MENU_SHOW, "Show CatCrewAI", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, MENU_QUIT, "Quit CatCrewAI", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &PredefinedMenuItem::separator(app)?, &quit_item])?;

    let tray = match app.tray_by_id(TRAY_ID) {
        Some(tray) => tray,
        None => {
            let mut builder = TrayIconBuilder::with_id(TRAY_ID).tooltip("CatCrewAI");
            if let Some(icon) = app.default_window_icon() {
                builder = builder.icon(icon.clone());
            }
            builder.build(app)?
        }
    };
    tray.set_menu(Some(menu))?;
    tray.set_show_menu_on_left_click(false)?; // left click toggles the window; right click opens the menu
    tray.on_menu_event(|app, event| match event.id().as_ref() {
        MENU_SHOW => show_main(app),
        MENU_QUIT => quit(app),
        _ => {}
    });
    tray.on_tray_icon_event(|tray, event| {
        if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
            toggle_main(tray.app_handle());
        }
    });
    Ok(())
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .manage(OwnedBackend(Mutex::new(None)))
        .manage(BackendInfo::new())
        .manage(Lifecycle::default())
        .invoke_handler(tauri::generate_handler![quit_app, backend_status])
        .on_window_event(|window, event| {
            // The "X" button hides to the tray. Once a real quit has started, let the close through.
            if window.label() != MAIN_WINDOW {
                return;
            }
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.app_handle().state::<Lifecycle>().is_quitting() {
                    return;
                }
                api.prevent_close();
                let _ = window.hide();
                notify_background(window.app_handle());
            }
        })
        .setup(|app| {
            setup_tray(app)?;

            let handle = app.handle().clone();
            thread::spawn(move || start_backend(&handle));

            // Failsafe: normally the UI shows the window after its first paint.
            let handle = app.handle().clone();
            thread::spawn(move || {
                thread::sleep(SHOW_FAILSAFE);
                if let Some(win) = handle.get_webview_window("main") {
                    if !win.is_visible().unwrap_or(true) {
                        let _ = win.show();
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the CatCrewAI shell");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_backend(handle);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    /// Serve one canned HTTP response on an ephemeral loopback port.
    fn serve_once(response: &'static str) -> (u16, thread::JoinHandle<String>) {
        let listener = TcpListener::bind((HOST, 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let handle = thread::spawn(move || {
            let (mut sock, _) = listener.accept().unwrap();
            let mut buf = [0u8; 2048];
            let n = sock.read(&mut buf).unwrap();
            sock.write_all(response.as_bytes()).unwrap();
            String::from_utf8_lossy(&buf[..n]).to_string()
        });
        (port, handle)
    }

    #[test]
    fn status_serialises_the_way_the_ui_reads_it() {
        assert_eq!(serde_json::to_string(&BackendStatus::Starting).unwrap(), r#"{"state":"starting"}"#);
        assert_eq!(serde_json::to_string(&BackendStatus::Ready).unwrap(), r#"{"state":"ready"}"#);
        assert_eq!(serde_json::to_string(&BackendStatus::External).unwrap(), r#"{"state":"external"}"#);
        assert_eq!(
            serde_json::to_string(&BackendStatus::Failed { detail: "boom".into() }).unwrap(),
            r#"{"state":"failed","detail":"boom"}"#
        );
    }

    #[test]
    fn the_output_tail_keeps_the_last_lines_and_trims_them() {
        let tail = Mutex::new(VecDeque::new());
        push_tail(&tail, "   ");
        push_tail(&tail, "");
        for i in 0..(TAIL_LINES + 5) {
            push_tail(&tail, &format!("line {i}  "));
        }
        push_tail(&tail, &"x".repeat(TAIL_LINE_MAX + 50));
        let text = tail_text(&tail);
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(lines.len(), TAIL_LINES);
        assert_eq!(lines[TAIL_LINES - 2], format!("line {}", TAIL_LINES + 4)); // trailing blanks trimmed
        assert_eq!(lines[TAIL_LINES - 1].len(), TAIL_LINE_MAX);
        assert!(!text.contains("line 0"));
    }

    #[test]
    fn failure_detail_appends_the_tail_only_when_there_is_one() {
        assert_eq!(failure_detail("It stopped.", ""), "It stopped.");
        assert_eq!(failure_detail("It stopped.", "  \n"), "It stopped.");
        assert_eq!(failure_detail("It stopped.", "Traceback\nImportError"), "It stopped.\n\nTraceback\nImportError");
    }

    const TICK: Duration = Duration::from_millis(5);

    #[test]
    fn wait_ends_when_the_engine_says_it_is_listening() {
        let (tx, rx) = mpsc::channel();
        tx.send(()).unwrap();
        assert_eq!(wait_until_ready(&rx, || ChildState::Running, || false, Duration::from_secs(5), TICK), Wait::Ready);
    }

    #[test]
    fn wait_reports_a_process_that_died_at_once_not_after_the_timeout() {
        let (tx, rx) = mpsc::channel::<()>();
        drop(tx); // the output pipes closed: must not spin
        let started = Instant::now();
        let out = wait_until_ready(&rx, || ChildState::Exited("exit code: 1".into()), || false, Duration::from_secs(30), TICK);
        assert_eq!(out, Wait::Exited("exit code: 1".into()));
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn wait_notices_a_healthy_engine_even_if_the_listening_line_was_missed() {
        let (_tx, rx) = mpsc::channel::<()>();
        let mut asked = 0;
        // health is only polled about once a second, so allow a couple of seconds
        let out = wait_until_ready(
            &rx,
            || ChildState::Running,
            || {
                asked += 1;
                true
            },
            Duration::from_secs(10),
            TICK,
        );
        assert_eq!(out, Wait::Ready);
        assert!(asked >= 1);
    }

    #[test]
    fn wait_times_out_when_nothing_happens_and_stops_when_the_app_quits() {
        let (_tx, rx) = mpsc::channel::<()>();
        assert_eq!(wait_until_ready(&rx, || ChildState::Running, || false, Duration::from_millis(60), TICK), Wait::TimedOut);
        assert_eq!(wait_until_ready(&rx, || ChildState::Gone, || false, Duration::from_secs(30), TICK), Wait::Quit);
    }

    #[test]
    fn tray_click_hides_a_visible_window_and_restores_a_hidden_or_minimized_one() {
        assert_eq!(tray_toggle(true, false), TrayToggle::Hide);
        assert_eq!(tray_toggle(false, false), TrayToggle::Show);
        assert_eq!(tray_toggle(true, true), TrayToggle::Show);
        assert_eq!(tray_toggle(false, true), TrayToggle::Show);
    }

    #[test]
    fn quit_starts_once_and_the_background_notice_shows_once() {
        let life = Lifecycle::default();
        assert!(!life.is_quitting());
        assert!(life.begin_quit());
        assert!(!life.begin_quit()); // a second quit (tray + button) is a no-op
        assert!(life.is_quitting());

        assert!(life.first_background_hide());
        assert!(!life.first_background_hide());
    }

    #[test]
    fn healthy_backend_is_detected() {
        let (port, seen) = serve_once("HTTP/1.1 200 OK\r\ncontent-type: application/json\r\nconnection: close\r\n\r\n{\"status\":\"ok\",\"version\":\"0.1.0\",\"busy\":false}");
        assert!(backend_healthy(port));
        let request = seen.join().unwrap();
        assert!(request.starts_with("GET /api/health HTTP/1.1\r\n"));
        // loopback Host header, and no Origin (the backend's local-only middleware allows that)
        assert!(request.contains(&format!("Host: {HOST}:{port}")));
        assert!(!request.to_lowercase().contains("origin:"));
    }

    #[test]
    fn other_servers_and_errors_are_not_a_backend() {
        let (port, _h) = serve_once("HTTP/1.1 403 Forbidden\r\nconnection: close\r\n\r\nnope");
        assert!(!backend_healthy(port));
        let (port, _h) = serve_once("HTTP/1.1 200 OK\r\nconnection: close\r\n\r\n<html>something else</html>");
        assert!(!backend_healthy(port));
    }

    #[test]
    fn nothing_listening_is_not_healthy() {
        let port = TcpListener::bind((HOST, 0)).unwrap().local_addr().unwrap().port();
        // the listener is dropped here, so the port is closed
        assert!(!backend_healthy(port));
        assert!(http("POST", "/api/shutdown", port).is_none());
    }

    #[test]
    fn shutdown_is_a_post() {
        let (port, seen) = serve_once("HTTP/1.1 202 Accepted\r\nconnection: close\r\n\r\n{\"status\":\"stopping\"}");
        let (status, body) = http("POST", "/api/shutdown", port).unwrap();
        assert_eq!(status, 202);
        assert!(body.contains("stopping"));
        assert!(seen.join().unwrap().starts_with("POST /api/shutdown HTTP/1.1"));
    }

    #[test]
    fn repo_root_points_at_the_directory_with_backend() {
        std::env::remove_var("CATCREW_REPO");
        assert!(repo_root().join("backend").join("__main__.py").is_file());
    }
}
