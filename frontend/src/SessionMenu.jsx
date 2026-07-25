import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

// The 3-dot "⋮" button on a chat/session row: opens a small dropdown with
// Rename (inline text field) and Delete (inline confirm) — shared by
// ChatSidebar and the History page so both behave identically.
//
// The dropdown is rendered into a portal (fixed-positioned off the trigger's
// own bounding rect) instead of as a normal absolutely-positioned child.
// Session rows live inside scrollable lists (`.session-list` has
// `overflow-y: auto`), and a plain absolutely-positioned dropdown gets
// clipped by that ancestor whenever it extends past the list's own
// content-height box — which it always does for the last (often only) row.
export default function SessionMenu({ session, onRename, onDelete }) {
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [value, setValue] = useState(session.title || "");
  const [busy, setBusy] = useState(false);
  const [coords, setCoords] = useState(null);
  const triggerRef = useRef(null);
  const dropdownRef = useRef(null);

  function closeAll() {
    setOpen(false);
    setRenaming(false);
    setConfirming(false);
  }

  useLayoutEffect(() => {
    if (!open) return;
    function place() {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (!rect) return;
      setCoords({ top: rect.bottom + 6, right: Math.max(8, window.innerWidth - rect.right) });
    }
    place();
    window.addEventListener("resize", place);
    // Reposition (don't close) on scroll — an autofocused rename input can
    // itself trigger a scroll-into-view, which must not slam the menu shut.
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  useEffect(() => {
    function onDocMouseDown(e) {
      if (triggerRef.current?.contains(e.target) || dropdownRef.current?.contains(e.target)) return;
      closeAll();
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, []);

  async function submitRename(e) {
    e.preventDefault();
    const title = value.trim();
    if (!title || title === session.title) {
      closeAll();
      return;
    }
    setBusy(true);
    try {
      await onRename(session.session_id, title);
      closeAll();
    } catch {
      // leave the form open so the user can retry
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    setBusy(true);
    try {
      await onDelete(session.session_id);
      closeAll();
    } catch {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        ref={triggerRef}
        className={`session-menu-trigger${open ? " open" : ""}`}
        aria-label="Chat options"
        onClick={(e) => {
          e.stopPropagation();
          setValue(session.title || "");
          setOpen((o) => !o);
          setRenaming(false);
          setConfirming(false);
        }}
      >
        ⋮
      </button>

      {open &&
        coords &&
        createPortal(
          <div
            className="session-menu-dropdown"
            ref={dropdownRef}
            style={{ top: coords.top, right: coords.right }}
            onClick={(e) => e.stopPropagation()}
          >
            {renaming ? (
              <form className="session-rename-form" onSubmit={submitRename}>
                <input
                  autoFocus
                  value={value}
                  disabled={busy}
                  onChange={(e) => setValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") closeAll();
                  }}
                />
                <button type="submit" disabled={busy}>
                  Save
                </button>
              </form>
            ) : confirming ? (
              <div className="session-menu-confirm">
                <span>Delete this chat?</span>
                <div className="session-menu-confirm-actions">
                  <button type="button" className="danger" onClick={confirmDelete} disabled={busy}>
                    {busy ? "Deleting…" : "Delete"}
                  </button>
                  <button type="button" onClick={() => setConfirming(false)} disabled={busy}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <>
                <button type="button" onClick={() => setRenaming(true)}>
                  Rename
                </button>
                <button type="button" className="danger" onClick={() => setConfirming(true)}>
                  Delete
                </button>
              </>
            )}
          </div>,
          document.body
        )}
    </>
  );
}
