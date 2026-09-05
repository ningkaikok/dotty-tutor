import { useEffect, useState } from "react";
import { loadRoster } from "../../api/classroom";
import { setCurrentLearnerId, useLearnerId } from "../../api/identity";
import type { RosterEntry } from "../../types/classroom";

/**
 * 学生端"我是谁"选择器。
 *
 * 存在的理由：老师在班级花名册里填的 learnerId 必须和学生端使用的 learnerId 是同一个
 * 值，作业指派和班级看板才能对上。此前学生端把身份写死成 `local-demo`，老师加进班级的
 * 学生永远收不到作业。
 *
 * **这不是登录。**下拉框里能看到全班的名字，任何人都可以选择成为任意一个学生，服务端
 * 不做校验也无法校验。它只服务于"一台机器、老师在场"的试用场景；接入登录后本组件连同
 * `api/identity.ts` 一起删除。
 *
 * 只挂在 StudentNav 上，因此只出现在列表级页面——做题和陪练进行中不会渲染它，避免学生
 * 在任务中途切换身份，把半份作答记到别人名下。
 */
export function StudentIdentityPicker() {
  const learnerId = useLearnerId();
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    loadRoster()
      .then((items) => {
        if (!cancelled) setRoster(items);
      })
      .catch(() => {
        // 花名册读不到不影响做题：保持当前身份、不显示选择器即可，不要弹错误打断学生。
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 还没有任何班级时不渲染任何东西，纯 Demo 场景与接入本组件之前完全一致。
  if (failed || roster.length === 0) return null;

  const known = roster.some((entry) => entry.learnerId === learnerId);

  return (
    <label className="student-identity">
      <span className="student-identity-label">我是</span>
      <select
        className="student-identity-select"
        value={learnerId}
        onChange={(event) => setCurrentLearnerId(event.target.value)}
      >
        {/* 当前身份不在花名册里时（默认 Demo 身份，或老师把人移出了班级）仍然列出它，
            否则 select 会显示成第一个同学，学生看到的身份和实际发出的请求不一致。 */}
        {!known && <option value={learnerId}>{learnerId}（不在花名册中）</option>}
        {roster.map((entry) => (
          <option key={`${entry.classId}:${entry.learnerId}`} value={entry.learnerId}>
            {entry.displayName}（{entry.className}）
          </option>
        ))}
      </select>
    </label>
  );
}
