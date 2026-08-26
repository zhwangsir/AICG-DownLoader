import { useEffect, useState } from "react";
import { Loader2, Lock, LockOpen, ShieldAlert } from "lucide-react";
import { changeNsfwPin, getNsfwStatus, setNsfwEnabled } from "../../api/client";
import { useDramaStore } from "../../store/useDramaStore";
import { modalScrollStyle } from "./shared";

/**
 * M27 NSFW 门禁模态：
 * - 未设 PIN：首次开启需设置管理 PIN（4-8 位数字，二次确认）
 * - 已设 PIN 且 NSFW 关：输入 PIN 解锁
 * - NSFW 开：输入 PIN 锁定；并可修改 PIN（旧 PIN + 新 PIN 二次确认）
 * 状态写回 store（nsfwEnabled/nsfwHasPin），NAS 模型库/搜索联动。
 */
export function NsfwGateModal({ onClose }: { onClose: () => void }) {
  const nsfwEnabled = useDramaStore((s) => s.nsfwEnabled);
  const nsfwHasPin = useDramaStore((s) => s.nsfwHasPin);
  const setNsfwState = useDramaStore((s) => s.setNsfwState);
  const setStatusInfo = useDramaStore((s) => s.setStatusInfo);

  const [pin, setPin] = useState("");
  const [newPin, setNewPin] = useState("");
  const [confirmPin, setConfirmPin] = useState("");
  const [showChangePin, setShowChangePin] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // 打开时从后端同步最新状态（防多端漂移）
  useEffect(() => {
    getNsfwStatus()
      .then((s) => setNsfwState(s.nsfw_enabled, s.has_pin))
      .catch(() => undefined);
  }, [setNsfwState]);

  const reset = () => {
    setPin("");
    setNewPin("");
    setConfirmPin("");
    setError("");
  };

  const apply = async (enabled: boolean) => {
    setError("");
    if (!nsfwHasPin && newPin !== confirmPin) {
      setError("两次输入的新 PIN 不一致");
      return;
    }
    setBusy(true);
    try {
      const s = await setNsfwEnabled(
        enabled,
        pin,
        nsfwHasPin ? undefined : newPin
      );
      setNsfwState(s.nsfw_enabled, s.has_pin);
      setStatusInfo(s.nsfw_enabled ? "NSFW 已解锁" : "NSFW 已锁定");
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const submitChangePin = async () => {
    setError("");
    if (newPin !== confirmPin) {
      setError("两次输入的新 PIN 不一致");
      return;
    }
    setBusy(true);
    try {
      await changeNsfwPin(pin, newPin);
      setStatusInfo("NSFW 管理 PIN 已修改");
      reset();
      setShowChangePin(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal nsfw-gate-modal"
        style={modalScrollStyle}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-title">
          <ShieldAlert size={16} style={{ marginRight: 6, verticalAlign: -3 }} />
          NSFW 内容门禁
        </div>

        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 14, lineHeight: 1.6 }}>
          {nsfwEnabled
            ? "NSFW 当前已解锁：NAS 模型库与模型搜索将显示成人内容。输入 PIN 可重新锁定。"
            : nsfwHasPin
              ? "输入管理 PIN 解锁 NSFW 内容（NAS 模型库浏览与模型下载）。"
              : "首次开启需设置管理 PIN（4-8 位数字），用于后续解锁/锁定/修改。"}
        </div>

        {nsfwHasPin && (
          <div className="modal-field">
            <label className="modal-label">管理 PIN</label>
            <input
              className="modal-input"
              type="password"
              inputMode="numeric"
              maxLength={8}
              placeholder="输入 PIN"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
            />
          </div>
        )}

        {(!nsfwHasPin || showChangePin) && (
          <>
            <div className="modal-field">
              <label className="modal-label">{nsfwHasPin ? "新 PIN" : "设置 PIN"}</label>
              <input
                className="modal-input"
                type="password"
                inputMode="numeric"
                maxLength={8}
                placeholder="4-8 位数字"
                value={newPin}
                onChange={(e) => setNewPin(e.target.value)}
              />
            </div>
            <div className="modal-field">
              <label className="modal-label">确认{nsfwHasPin ? "新" : ""} PIN</label>
              <input
                className="modal-input"
                type="password"
                inputMode="numeric"
                maxLength={8}
                placeholder="再次输入"
                value={confirmPin}
                onChange={(e) => setConfirmPin(e.target.value)}
              />
            </div>
          </>
        )}

        {error && <div className="modal-error">{error}</div>}

        <div className="modal-actions">
          {nsfwEnabled && !showChangePin && (
            <button
              className="modal-btn"
              disabled={busy}
              onClick={() => {
                reset();
                setShowChangePin(true);
              }}
            >
              修改 PIN
            </button>
          )}
          {showChangePin ? (
            <>
              <button className="modal-btn" disabled={busy} onClick={() => { reset(); setShowChangePin(false); }}>
                返回
              </button>
              <button className="modal-btn modal-btn-primary" disabled={busy} onClick={submitChangePin}>
                {busy ? <Loader2 size={13} className="spin" /> : "确认修改"}
              </button>
            </>
          ) : nsfwEnabled ? (
            <button className="modal-btn modal-btn-primary" disabled={busy} onClick={() => apply(false)}>
              {busy ? <Loader2 size={13} className="spin" /> : <Lock size={13} />} 锁定 NSFW
            </button>
          ) : (
            <button className="modal-btn modal-btn-primary" disabled={busy} onClick={() => apply(true)}>
              {busy ? <Loader2 size={13} className="spin" /> : <LockOpen size={13} />}
              {nsfwHasPin ? "解锁 NSFW" : "设置 PIN 并解锁"}
            </button>
          )}
          <button className="modal-btn" disabled={busy} onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
