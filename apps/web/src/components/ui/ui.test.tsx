// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { Button, TextField, Notice } from "./index";
afterEach(cleanup);
it("user Given a busy save When activating it with keyboard Then it stays disabled and announces progress", async () => {
  render(<Button busy>保存课程</Button>);
  const button = screen.getByRole("button", { name: "保存课程" });
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("aria-busy", "true");
  await userEvent.tab();
  expect(button).not.toHaveFocus();
});
it("user Given an invalid field When navigating by keyboard Then its label error and hint remain associated", async () => {
  render(<TextField label="教材名称" hint="使用原书标题" error="请填写教材名称" required />);
  const input = screen.getByRole("textbox", { name: "教材名称" });
  expect(input).toHaveAccessibleDescription("使用原书标题 请填写教材名称");
  expect(input).toHaveAttribute("aria-invalid", "true");
  await userEvent.tab();
  expect(input).toHaveFocus();
});
it("user Given a request failure When feedback appears Then it is announced as an alert", () => {
  render(<Notice tone="danger">读取失败，请重试</Notice>);
  expect(screen.getByRole("alert")).toHaveTextContent("读取失败，请重试");
});
