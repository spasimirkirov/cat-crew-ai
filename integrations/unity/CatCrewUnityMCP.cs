// CatCrewAI — starts the local Unity MCP server when the Unity project loads.
//
// Put this file in  <your project>/Assets/Editor/  (integrations/unity/install_unity_helper.py does that).
// It needs the "MCP for Unity" package (https://github.com/CoplayDev/unity-mcp) in the project; the package is
// reached by reflection, so this script still compiles — and just logs what is missing — when it is not installed.
//
// What it does on every editor start / script reload (when auto-start is on):
//   1. Points MCP for Unity at the HTTP transport (EditorPrefs) so it connects to http://localhost:8080.
//   2. If nothing answers on that port yet, starts the local server: through the package's own service
//      (MCPServiceLocator.Server.StartLocalHttpServer), or, when that is not available, `uvx --from mcpforunityserver
//      mcp-for-unity --transport http --http-url http://localhost:8080` as a hidden child process.
//   3. Stops the child process again when the Editor quits.
// CatCrewAI (Pulse) then reaches the server at http://localhost:8080/mcp — see backend/config/mcp_servers.json.
//
// Menu: CatCrewAI > Unity MCP > Start / Stop / Status / Auto-start.

#if UNITY_EDITOR
using System;
using System.Diagnostics;
using System.Net.Sockets;
using System.Reflection;
using UnityEditor;
using Debug = UnityEngine.Debug;

namespace CatCrewAI
{
    [InitializeOnLoad]
    public static class CatCrewUnityMCP
    {
        const string BaseUrl = "http://localhost:8080";        // the server's address; the MCP endpoint is BaseUrl + "/mcp"
        const string Host = "localhost";
        const int Port = 8080;

        const string AutoStartPref = "CatCrewAI.UnityMCP.AutoStart";   // per machine, default on
        const string PidKey = "CatCrewAI.UnityMCP.ChildPid";           // survives script reloads within one editor session
        const string PackageStartedKey = "CatCrewAI.UnityMCP.StartedViaPackage";
        const string LoggedKey = "CatCrewAI.UnityMCP.LoggedThisSession";

        static CatCrewUnityMCP()
        {
            EditorApplication.quitting += StopOnQuit;
            // delayCall: the editor is not ready to start processes or read prefs while it is still loading assemblies.
            EditorApplication.delayCall += AutoStart;
        }

        static bool AutoStartEnabled
        {
            get { return EditorPrefs.GetBool(AutoStartPref, true); }
            set { EditorPrefs.SetBool(AutoStartPref, value); }
        }

        // ------------------------------------------------------------------------------------------------ menu

        [MenuItem("CatCrewAI/Unity MCP/Start server")]
        static void MenuStart() { Start(true); }

        [MenuItem("CatCrewAI/Unity MCP/Stop server")]
        static void MenuStop() { Stop(true); }

        [MenuItem("CatCrewAI/Unity MCP/Status")]
        static void MenuStatus()
        {
            Debug.Log("[CatCrewAI] Unity MCP: " + Describe());
        }

        [MenuItem("CatCrewAI/Unity MCP/Auto-start on project load")]
        static void MenuToggleAuto() { AutoStartEnabled = !AutoStartEnabled; }

        [MenuItem("CatCrewAI/Unity MCP/Auto-start on project load", true)]
        static bool MenuToggleAutoValidate()
        {
            Menu.SetChecked("CatCrewAI/Unity MCP/Auto-start on project load", AutoStartEnabled);
            return true;
        }

        // ------------------------------------------------------------------------------------------------ start / stop

        static void AutoStart()
        {
            if (!AutoStartEnabled || UnityEngine.Application.isBatchMode) return; // never in CI / batch builds
            Start(false);
        }

        /// <summary>Make sure the local MCP server is running. `verbose` also logs the "already running" case.</summary>
        public static void Start(bool verbose)
        {
            ConfigurePackage();

            if (IsReachable())
            {
                if (verbose || !SessionState.GetBool(LoggedKey, false))
                    Debug.Log("[CatCrewAI] Unity MCP server is running at " + BaseUrl + "/mcp");
                SessionState.SetBool(LoggedKey, true);
                return;
            }

            if (TryStartViaPackage()) return;
            StartViaUvx();
        }

        public static void Stop(bool verbose)
        {
            bool stopped = false;
            if (SessionState.GetBool(PackageStartedKey, false))
            {
                stopped = InvokeServer("StopLocalHttpServer");
                SessionState.SetBool(PackageStartedKey, false);
            }
            int pid = SessionState.GetInt(PidKey, 0);
            if (pid > 0)
            {
                try
                {
                    Process child = Process.GetProcessById(pid);
                    if (!child.HasExited) { child.Kill(); stopped = true; }
                }
                catch (Exception) { /* already gone */ }
                SessionState.SetInt(PidKey, 0);
            }
            if (verbose)
                Debug.Log(stopped
                    ? "[CatCrewAI] Unity MCP server stopped."
                    : "[CatCrewAI] Unity MCP: nothing to stop (this helper only stops a server it started itself).");
        }

        static void StopOnQuit()
        {
            // Only what we started: a server the user runs by hand keeps running.
            Stop(false);
        }

        // ------------------------------------------------------------------------------------------------ MCP for Unity

        /// <summary>Tell MCP for Unity to use the HTTP transport and to start its bridge on load (best effort: keys of the package).</summary>
        static void ConfigurePackage()
        {
            if (FindType("MCPForUnity.Editor.Services.MCPServiceLocator") == null) return;
            EditorPrefs.SetBool("MCPForUnity.UseHttpTransport", true);
            EditorPrefs.SetString("MCPForUnity.HttpUrl", BaseUrl);
            EditorPrefs.SetBool("MCPForUnity.AutoStartOnLoad", true);
        }

        static bool TryStartViaPackage()
        {
            if (FindType("MCPForUnity.Editor.Services.MCPServiceLocator") == null)
            {
                Debug.LogWarning("[CatCrewAI] Unity MCP: the 'MCP for Unity' package is not in this project. Add it from "
                    + "https://github.com/CoplayDev/unity-mcp (Package Manager > Add package from git URL). "
                    + "Trying the standalone server instead.");
                return false;
            }
            if (!InvokeServer("StartLocalHttpServer"))
                return false;
            SessionState.SetBool(PackageStartedKey, true);
            Debug.Log("[CatCrewAI] Unity MCP server starting at " + BaseUrl + "/mcp (through MCP for Unity).");
            return true;
        }

        static void StartViaUvx()
        {
            int existing = SessionState.GetInt(PidKey, 0);
            if (existing > 0 && IsAlive(existing)) return; // already launched, still starting up

            try
            {
                var info = new ProcessStartInfo
                {
                    FileName = "uvx",
                    Arguments = "--from mcpforunityserver mcp-for-unity --transport http --http-url " + BaseUrl,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                };
                Process child = Process.Start(info);
                if (child == null) throw new InvalidOperationException("the process did not start");
                SessionState.SetInt(PidKey, child.Id);
                Debug.Log("[CatCrewAI] Unity MCP server launched with uvx (pid " + child.Id + "), listening at " + BaseUrl + "/mcp.");
            }
            catch (Exception ex)
            {
                Debug.LogError("[CatCrewAI] Could not start the Unity MCP server: " + ex.Message
                    + "\nInstall uv (https://docs.astral.sh/uv/) so 'uvx' is on PATH, or start the server yourself: "
                    + "uvx --from mcpforunityserver mcp-for-unity --transport http --http-url " + BaseUrl);
            }
        }

        /// <summary>Call a method on MCPServiceLocator.Server (with `true` when it takes one bool). False if anything is missing or it failed.</summary>
        static bool InvokeServer(string method)
        {
            try
            {
                Type locator = FindType("MCPForUnity.Editor.Services.MCPServiceLocator");
                if (locator == null) return false;
                PropertyInfo prop = locator.GetProperty("Server", BindingFlags.Public | BindingFlags.Static);
                object server = prop == null ? null : prop.GetValue(null, null);
                if (server == null) return false;

                foreach (MethodInfo m in server.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance))
                {
                    if (m.Name != method) continue;
                    ParameterInfo[] ps = m.GetParameters();
                    object[] args;
                    if (ps.Length == 0) args = new object[0];
                    else if (ps.Length == 1 && ps[0].ParameterType == typeof(bool)) args = new object[] { true };
                    else continue;
                    object result = m.Invoke(server, args);
                    return !(result is bool) || (bool)result;
                }
                Debug.LogWarning("[CatCrewAI] Unity MCP: this version of MCP for Unity has no " + method + "() to call.");
            }
            catch (Exception ex)
            {
                Debug.LogWarning("[CatCrewAI] Unity MCP: " + method + " failed: " + ex.GetBaseException().Message);
            }
            return false;
        }

        // ------------------------------------------------------------------------------------------------ helpers

        static Type FindType(string fullName)
        {
            foreach (Assembly asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type t = asm.GetType(fullName, false);
                if (t != null) return t;
            }
            return null;
        }

        /// <summary>True when something accepts a connection on the server's port (a 400 ms check on the main thread).</summary>
        static bool IsReachable()
        {
            try
            {
                using (var client = new TcpClient())
                {
                    IAsyncResult pending = client.BeginConnect(Host, Port, null, null);
                    if (!pending.AsyncWaitHandle.WaitOne(400)) return false;
                    client.EndConnect(pending);
                    return client.Connected;
                }
            }
            catch (Exception) { return false; }
        }

        static bool IsAlive(int pid)
        {
            try { return !Process.GetProcessById(pid).HasExited; }
            catch (Exception) { return false; }
        }

        static string Describe()
        {
            string package = FindType("MCPForUnity.Editor.Services.MCPServiceLocator") != null ? "MCP for Unity installed" : "MCP for Unity NOT installed";
            string server = IsReachable() ? "server reachable at " + BaseUrl + "/mcp" : "server not reachable at " + BaseUrl;
            int pid = SessionState.GetInt(PidKey, 0);
            string child = pid > 0 && IsAlive(pid) ? ", helper started it (pid " + pid + ")" : "";
            return package + "; " + server + child + "; auto-start " + (AutoStartEnabled ? "on" : "off") + ".";
        }
    }
}
#endif
