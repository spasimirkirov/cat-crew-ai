import { faChevronDown, faCoins } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useId, useState } from "react";

const STRATEGY: { role: string; pick: string; why: string }[] = [
  { role: "Chat / Dispatcher", pick: "A light, fast model: Groq, Gemini Flash, or a cheap OpenRouter model", why: "Most turns are short routing and conversation, so run them for very little." },
  { role: "Coding", pick: "OpenRouter: deepseek/deepseek-v4-pro or anthropic/claude-sonnet", why: "Spend on the model that makes the surgical edits, where a mistake is costly." },
  { role: "Memory / Summarization", pick: "OpenRouter: google/gemini-flash (long context, low cost)", why: "Reads a lot and writes a little. Long chats are compressed here before they reach a costly model." },
  { role: "Planning", pick: "OpenRouter: deepseek/deepseek-r1 or a reasoning model", why: "A reasoning model is slow and dear. Use it only for the first step-by-step breakdown of a project." },
  { role: "Review", pick: "A strong tool-calling model from a different vendor than Coding", why: "QA checks the build with it; a second model family catches mistakes the coder's own model overlooks." },
  { role: "Vision", pick: "OpenRouter: google/gemini-flash or a Qwen-VL model", why: "Only used when Aegis or Pixel needs a screenshot described, not on every turn." },
];

/** A collapsed-by-default guide to spreading the roles over cheap and expensive models. */
export function HybridStrategyPanel() {
  const uid = useId();
  const [open, setOpen] = useState(false);

  return (
    <section aria-labelledby={`${uid}-h`} className="card p-5">
      <h2 id={`${uid}-h`} className="text-sm font-semibold">
        <button
          type="button"
          className="flex w-full items-center gap-2 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan/60"
          aria-expanded={open}
          aria-controls={`${uid}-body`}
          onClick={() => setOpen((o) => !o)}
        >
          <FontAwesomeIcon icon={faCoins} className="text-cyan" aria-hidden />
          <span className="flex-1">Recommended Hybrid Strategy &amp; Token Optimization</span>
          <FontAwesomeIcon icon={faChevronDown} className={clsx("text-xs text-muted transition", open && "rotate-180")} aria-hidden />
        </button>
      </h2>
      {open && (
        <div id={`${uid}-body`} className="mt-4">
          <p className="text-xs text-muted">
            Give each role the cheapest model that does its job well. A role you leave unset follows Chat, so you only need to set the ones that should differ.
          </p>
          <dl className="mt-3 grid gap-3 md:grid-cols-2">
            {STRATEGY.map((s) => (
              <div key={s.role} className="rounded-lg border border-line bg-white/[0.02] px-3 py-2.5">
                <dt className="text-sm font-medium">{s.role}</dt>
                <dd className="mt-0.5 text-xs text-cyan">{s.pick}</dd>
                <dd className="mt-1 text-xs text-muted">{s.why}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-muted">
            <strong className="font-medium text-ink">Automatic saving:</strong> when a chat grows past 8,000 tokens, the Memory model first summarises the older turns in the background.
            The costly model then receives the summary plus the most recent messages instead of the whole history.
          </p>
        </div>
      )}
    </section>
  );
}
