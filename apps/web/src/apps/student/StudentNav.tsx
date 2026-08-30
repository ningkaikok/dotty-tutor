import { NavLink } from "react-router";
import "./student.css";

const NAV_ITEMS = [
  { to: "/learn", label: "今日" },
  { to: "/mistakes", label: "错题" },
  { to: "/mistakes/progress", label: "复习" },
] as const;

/**
 * 学生端持久导航。
 *
 * 只出现在列表级页面（/learn、/mistakes 列表态、/mistakes/progress）。
 * 做题、拍照录入、确认识别结果、陪练答疑这些“任务进行中”的屏幕不渲染它——
 * 三个跳转入口会鼓励学生在任务中途跳走，所以这些屏幕只保留单一返回按钮，
 * 由各自页面自己处理（见 MistakeCoachApp.tsx 的 returnsToStudentHome）。
 *
 * 用 `end` 精确匹配：否则 /mistakes 会在 /mistakes/progress 下也被判定为
 * active，两个入口同时高亮，学生分不清自己在哪一层。
 */
export function StudentNav() {
  return (
    <nav className="student-nav" aria-label="学习导航">
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end
          className={({ isActive }) => `student-nav-link${isActive ? " active" : ""}`}
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
