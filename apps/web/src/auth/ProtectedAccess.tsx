import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { setProtectedSession, type ProtectedSession } from "../api/identity";

type GateState = "checking" | "demo" | "login" | "ready" | "failed";

async function responseError(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: string; message?: string };
    return body.message || body.detail || `请求失败（${response.status}）`;
  } catch {
    return `请求失败（${response.status}）`;
  }
}

export function ProtectedAccess({ children }: { children: ReactNode }) {
  const [state, setState] = useState<GateState>("checking");
  const [session, setSession] = useState<ProtectedSession | null>(null);
  const [loginRole, setLoginRole] = useState<"teacher" | "student">("teacher");
  const [credential, setCredential] = useState("");
  const [learnerId, setLearnerId] = useState("");
  const [inviteToken, setInviteToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [issuedInvite, setIssuedInvite] = useState("");

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const configResponse = await fetch("/api/auth/config", { cache: "no-store" });
        if (!configResponse.ok) throw new Error(await responseError(configResponse));
        const config = await configResponse.json() as { protected: boolean };
        if (!config.protected) {
          if (active) setState("demo");
          return;
        }
        const response = await fetch("/api/auth/sessions", { cache: "no-store" });
        if (response.status === 401) {
          if (active) setState("login");
          return;
        }
        if (!response.ok) throw new Error(await responseError(response));
        const restored = await response.json() as ProtectedSession;
        if (active) {
          setSession(restored);
          setProtectedSession(restored);
          setState("ready");
        }
      } catch (requestError) {
        if (active) {
          setError(requestError instanceof Error ? requestError.message : "无法连接身份服务");
          setState("failed");
        }
      }
    })();
    return () => { active = false; };
  }, []);

  const login = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/auth/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(loginRole === "teacher" ? { teacherSecret: credential } : { inviteToken }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const authenticated = await response.json() as ProtectedSession;
      setSession(authenticated);
      setProtectedSession(authenticated);
      setCredential("");
      setInviteToken("");
      setState("ready");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "登录失败");
    } finally {
      setBusy(false);
    }
  };

  const logout = async () => {
    await fetch("/api/auth/sessions", { method: "DELETE" });
    setProtectedSession(null);
    setSession(null);
    setState("login");
  };

  const createInvite = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    setIssuedInvite("");
    try {
      const response = await fetch("/api/auth/invites", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ learnerId: learnerId.trim() }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const payload = await response.json() as { inviteToken: string };
      setIssuedInvite(payload.inviteToken);
      setLearnerId("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "邀请签发失败");
    }
  };

  if (state === "checking") return <main className="center-state"><span>正在检查登录状态…</span></main>;
  if (state === "failed") return <main className="center-state"><p role="alert">{error}</p><button onClick={() => location.reload()}>重试</button></main>;
  if (state === "login") return (
    <main className="center-state">
      <section className="panel" style={{ width: "min(100%, 30rem)", padding: "2rem", textAlign: "left" }}>
        <h1>登录 Dotty Tutor</h1>
        <p>学生使用教师签发的一次性邀请；教师使用本机配置的引导凭证。</p>
        <div role="group" aria-label="登录角色">
          <button type="button" aria-pressed={loginRole === "teacher"} onClick={() => setLoginRole("teacher")}>教师</button>
          <button type="button" aria-pressed={loginRole === "student"} onClick={() => setLoginRole("student")}>学生</button>
        </div>
        <form onSubmit={login}>
          <label>
            {loginRole === "teacher" ? "教师引导凭证" : "学生邀请令牌"}
            <input
              type="password"
              autoComplete="off"
              required
              value={loginRole === "teacher" ? credential : inviteToken}
              onChange={(event) => loginRole === "teacher" ? setCredential(event.target.value) : setInviteToken(event.target.value)}
            />
          </label>
          <button disabled={busy}>{busy ? "正在登录…" : "登录"}</button>
        </form>
        {error && <p role="alert">{error}</p>}
      </section>
    </main>
  );

  return (
    <>
      {state === "ready" && <div className="auth-session-bar">
        <span>{session?.role === "teacher" ? "教师会话" : `学生 ${session?.learnerId ?? ""}`}</span>
        {session?.role === "teacher" && <details>
          <summary>签发学生邀请</summary>
          <form onSubmit={createInvite}>
            <label>学生 ID <input required maxLength={128} value={learnerId} onChange={(event) => setLearnerId(event.target.value)} /></label>
            <button>生成 24 小时邀请</button>
          </form>
          {issuedInvite && <p>请安全交给该学生（仅显示一次）：<code>{issuedInvite}</code></p>}
        </details>}
        <button onClick={() => void logout()}>退出登录</button>
      </div>}
      {error && state === "ready" && <p role="alert">{error}</p>}
      {state === "demo" || state === "ready" ? children : null}
    </>
  );
}
