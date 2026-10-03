// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChapterSourceForm } from "./ChapterSourceForm";

describe("ChapterSourceForm", () => {
  afterEach(cleanup);

  it("user Given one page of existing OCR When creating a chapter Then it keeps the page and source version attached", () => {
    const onSubmit = vi.fn();
    render(<ChapterSourceForm libraries={[]} submitLabel="创建章节" onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText("章节名称"), { target: { value: "分数比较" } });
    fireEvent.change(screen.getByLabelText("起始页"), { target: { value: "12" } });
    fireEvent.change(screen.getByLabelText("结束页"), { target: { value: "12" } });
    fireEvent.change(screen.getByLabelText("来源版本"), { target: { value: "edition-2" } });
    fireEvent.change(screen.getByLabelText("来源许可说明"), { target: { value: "校内授权" } });
    fireEvent.change(screen.getByLabelText("OCR 原文"), { target: { value: "同分母分数比较分子。" } });
    fireEvent.submit(screen.getByRole("form", { name: "章节来源" }));

    expect(onSubmit).toHaveBeenCalledWith({
      title: "分数比较",
      subject: "math",
      source: { sourceVersion: "edition-2", license: "校内授权", pageStart: 12, pageEnd: 12, pages: [{ page: 12, text: "同分母分数比较分子。", regions: [], flags: [] }] },
    });
  });

  it("user Given pasted OCR spans several pages When submitting a single-page source form Then the form explains how to choose a library range", () => {
    const onSubmit = vi.fn();
    render(<ChapterSourceForm libraries={[]} submitLabel="创建章节" onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("章节名称"), { target: { value: "分数比较" } });
    fireEvent.change(screen.getByLabelText("来源许可说明"), { target: { value: "校内授权" } });
    fireEvent.change(screen.getByLabelText("起始页"), { target: { value: "12" } });
    fireEvent.change(screen.getByLabelText("结束页"), { target: { value: "13" } });
    fireEvent.change(screen.getByLabelText("OCR 原文"), { target: { value: "已粘贴的原文" } });
    fireEvent.click(screen.getByRole("button", { name: "创建章节" }));

    expect(screen.getByRole("alert")).toHaveTextContent("跨页内容请选择教材库中的已识别教材");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("user Given an oversized page range When marking a source issue Then the form rejects the range before building page flags", () => {
    const onSubmit = vi.fn();
    render(<ChapterSourceForm libraries={[]} submitLabel="创建章节" onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("章节名称"), { target: { value: "函数代入" } });
    fireEvent.change(screen.getByLabelText("来源许可说明"), { target: { value: "校内授权" } });
    fireEvent.change(screen.getByLabelText("起始页"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("结束页"), { target: { value: "100000000" } });
    fireEvent.click(screen.getByLabelText("可能缺少条件"));
    fireEvent.submit(screen.getByRole("form", { name: "章节来源" }));

    expect(screen.getByRole("alert")).toHaveTextContent("一次最多选择 80 页教材来源");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("user Given a region extends outside its page When saving the source Then the coordinates must be corrected first", () => {
    const onSubmit = vi.fn();
    render(<ChapterSourceForm libraries={[]} submitLabel="创建章节" onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("章节名称"), { target: { value: "函数代入" } });
    fireEvent.change(screen.getByLabelText("来源许可说明"), { target: { value: "校内授权" } });
    fireEvent.change(screen.getByLabelText("起始页"), { target: { value: "12" } });
    fireEvent.change(screen.getByLabelText("结束页"), { target: { value: "12" } });
    fireEvent.change(screen.getByRole("textbox", { name: "OCR 原文" }), { target: { value: "函数原文" } });
    fireEvent.click(screen.getByLabelText("标记原文所在区域"));
    fireEvent.change(screen.getByLabelText("x"), { target: { value: "0.8" } });
    fireEvent.click(screen.getByRole("button", { name: "创建章节" }));

    expect(screen.getByRole("alert")).toHaveTextContent("区域坐标必须位于页面范围内");
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
