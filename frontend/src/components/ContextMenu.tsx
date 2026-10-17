import { faClone, faCopy, faPaste, faScissors, faICursor } from "@fortawesome/free-solid-svg-icons";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useCallback, useEffect, useRef, useState } from "react";

// CatCrewAI's own right-click menu. The webview's default one (Back, Reload, Inspect...) is about a
// browser, not about this app, so it is suppressed everywhere. What is left is what a right click can
// usefully do here: cut / copy / paste / select all in text fields, and copy for selected text.
// In a development build, Shift + right click still opens the webview's menu (to inspect an element).

type Editable = HTMLInputElement | HTMLTextAreaElement | HTMLElement;

const TEXT_INPUT_TYPES = new Set(["text", "search", "url", "email", "tel", "password", "number", ""]);

/** The text field (or contenteditable) a right click landed in, or null. */
export function editableOf(target: EventTarget | null): Editable | null {
  if (!(target instanceof Element)) return null;
  const el = target.closest("input, textarea, [contenteditable]");
  if (!el) return null;
  if (el instanceof HTMLInputElement) return TEXT_INPUT_TYPES.has((el.getAttribute("type") ?? "text").toLowerCase()) ? el : null;
  if (el instanceof HTMLTextAreaElement) return el;
  const ce = el.getAttribute("contenteditable");
  return ce === "" || ce === "true" || ce === "plaintext-only" ? (el as HTMLElement) : null;
}

function selectedText(field: Editable | null): string {
  if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) {
    const { selectionStart: a, selectionEnd: b } = field;
    return a !== null && b !== null && a !== b ? field.value.slice(Math.min(a, b), Math.max(a, b)) : "";
  }
  return window.getSelection()?.toString() ?? "";
}

interface MenuState {
  x: number;
  y: number;
  field: Editable | null;
  text: string;
  password: boolean;
  readOnly: boolean;
}

interface Item {
  id: string;
  label: string;
  icon: IconDefinition;
  hint?: string;
  disabled?: boolean;
  run: () => void | Promise<void>;
}

const MOD = typeof navigator !== "undefined" && /mac/i.test(navigator.platform) ? "⌘" : "Ctrl+";

export function ContextMenu() {
  const [menu, setMenu] = useState<MenuState | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(0);

  const close = useCallback(() => setMenu(null), []);

  useEffect(() => {
    const onContext = (e: MouseEvent) => {
      if (e.shiftKey && import.meta.env.DEV) return; // developers keep the native menu (Inspect)
      e.preventDefault();
      const field = editableOf(e.target);
      const text = selectedText(field);
      const password = field instanceof HTMLInputElement && field.type === "password";
      const readOnly = Boolean(field && ((field as HTMLInputElement).readOnly || (field as HTMLInputElement).disabled));
      if (!field && !text) {
        setMenu(null); // nothing useful to offer: no menu at all
        return;
      }
      setMenu({ x: e.clientX, y: e.clientY, field, text, password, readOnly });
      setActive(0);
    };
    document.addEventListener("contextmenu", onContext);
    return () => document.removeEventListener("contextmenu", onContext);
  }, []);

  // Anything else the user does dismisses it.
  useEffect(() => {
    if (!menu) return;
    const away = (e: Event) => {
      if (!root.current?.contains(e.target as Node)) close();
    };
    window.addEventListener("mousedown", away, true);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("mousedown", away, true);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("blur", close);
    };
  }, [menu, close]);

  // Keep the menu inside the window.
  useEffect(() => {
    const el = root.current;
    if (!menu || !el) return;
    const { width, height } = el.getBoundingClientRect();
    el.style.left = `${Math.max(4, Math.min(menu.x, window.innerWidth - width - 4))}px`;
    el.style.top = `${Math.max(4, Math.min(menu.y, window.innerHeight - height - 4))}px`;
  }, [menu]);

  if (!menu) return null;

  const { field, text, password, readOnly } = menu;
  const refocus = () => field?.focus();

  const copy = async () => {
    refocus();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      document.execCommand("copy");
    }
  };
  const cut = async () => {
    refocus();
    if (!text) return;
    if (!document.execCommand("cut")) {
      await navigator.clipboard.writeText(text).catch(() => undefined);
      document.execCommand("delete");
    }
  };
  const paste = async () => {
    refocus();
    try {
      const clip = await navigator.clipboard.readText();
      if (clip) document.execCommand("insertText", false, clip); // fires a real input event, so React sees it
    } catch {
      /* the webview refused clipboard access: nothing to paste */
    }
  };
  const selectAll = () => {
    refocus();
    if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) field.select();
    else document.execCommand("selectAll");
  };

  const items: Item[] = field
    ? [
        { id: "cut", label: "Cut", icon: faScissors, hint: `${MOD}X`, disabled: readOnly || password || !text, run: cut },
        { id: "copy", label: "Copy", icon: faCopy, hint: `${MOD}C`, disabled: password || !text, run: copy },
        { id: "paste", label: "Paste", icon: faPaste, hint: `${MOD}V`, disabled: readOnly, run: paste },
        { id: "all", label: "Select all", icon: faICursor, hint: `${MOD}A`, run: selectAll },
      ]
    : [{ id: "copy", label: "Copy", icon: faClone, hint: `${MOD}C`, run: copy }];
  const enabled = items.filter((i) => !i.disabled);

  const choose = (item: Item) => {
    if (item.disabled) return;
    close();
    void item.run();
  };

  return (
    <div
      ref={root}
      role="menu"
      aria-label="Edit"
      tabIndex={-1}
      autoFocus
      style={{ left: menu.x, top: menu.y }}
      onMouseDown={(e) => e.preventDefault()} // keep the field's focus and selection
      onKeyDown={(e) => {
        if (e.key === "Escape") close();
        else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          const dir = e.key === "ArrowDown" ? 1 : -1;
          setActive((a) => (a + dir + enabled.length) % enabled.length);
        } else if (e.key === "Enter") {
          e.preventDefault();
          if (enabled[active]) choose(enabled[active]);
        }
      }}
      className="fixed z-[1000] min-w-[180px] rounded-xl border border-line bg-[#141824]/95 p-1 text-sm shadow-[0_12px_40px_rgba(0,0,0,.55)] backdrop-blur"
    >
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          role="menuitem"
          aria-disabled={item.disabled || undefined}
          disabled={item.disabled}
          onClick={() => choose(item)}
          onMouseEnter={() => {
            const i = enabled.indexOf(item);
            if (i >= 0) setActive(i);
          }}
          className={clsx(
            "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left transition",
            item.disabled ? "cursor-default text-muted/40" : enabled[active] === item ? "bg-cyan/15 text-cyan" : "text-ink hover:bg-white/5",
          )}
        >
          <FontAwesomeIcon icon={item.icon} className="w-3.5 shrink-0" aria-hidden />
          <span className="flex-1">{item.label}</span>
          {item.hint && <span className="text-[11px] text-muted/70">{item.hint}</span>}
        </button>
      ))}
    </div>
  );
}
