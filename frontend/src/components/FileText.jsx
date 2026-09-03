import { useRef, useState } from "react";
import { api } from "../api";

/* Drop a PDF, get editable text.
 *
 * The extracted text is SHOWN rather than swallowed, and that is the whole
 * reason this is a component instead of a hidden upload. A scan with no text
 * layer looks identical to a working PDF until something tries to read it —
 * and an interview built silently on an empty CV is worse than one that
 * refused. Here the operator sees exactly what the panel will read, and can
 * paste over it when the answer is "nothing".
 */
export default function FileText({
  id, label, hint, value, onChange, rows = 6, placeholder,
}) {
  const input = useRef(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [from, setFrom] = useState("");

  const take = async (file) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const { text, filename } = await api.extract(file);
      onChange(text);
      setFrom(filename);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>

      <div
        className={`drop compact ${over ? "over" : ""} ${from ? "filled" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          take(e.dataTransfer.files?.[0]);
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") input.current?.click();
        }}
      >
        <input
          ref={input} type="file" accept=".pdf,.txt,.md,.rtf"
          onChange={(e) => take(e.target.files?.[0])}
        />
        {busy ? (
          <b><span className="spinner" /> Reading…</b>
        ) : from ? (
          <>
            <b>{from}</b>
            <span>{value.length.toLocaleString()} characters · click to replace</span>
          </>
        ) : (
          <>
            <b>Drop a PDF here, or click to choose</b>
            <span>Or type into the box below</span>
          </>
        )}
      </div>

      {error && <div className="notice error" style={{ marginTop: 10 }}>{error}</div>}

      <textarea
        id={id} rows={rows} value={value} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        style={{ marginTop: 10 }}
      />
      {hint && <p className="hint">{hint}</p>}
    </div>
  );
}
