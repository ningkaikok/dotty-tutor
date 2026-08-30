// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { MistakeAttribution as MistakeAttributionData } from "../attribution";
import { MistakeAttribution } from "./MistakeAttribution";

describe("MistakeAttribution", () => {
  afterEach(cleanup);

  it("一致时给出确认式反馈", () => {
    render(<MistakeAttribution attribution={{ selfAssessment: "calculation", aiAssessment: "calculation" }} />);
    expect(screen.getByText("你的判断和 Dotty 一致：计算失误")).toBeInTheDocument();
  });

  it("不同时并列展示两种归因并保留学习契机", () => {
    render(<MistakeAttribution attribution={{ selfAssessment: "calculation", aiAssessment: "concept" }} />);
    expect(screen.getByText(/你觉得是「计算失误」/)).toBeInTheDocument();
    expect(screen.getByText(/Dotty 判断是「概念不清」/)).toBeInTheDocument();
    expect(screen.getByText("你的自评")).toBeInTheDocument();
    expect(screen.getByText("Dotty 判断")).toBeInTheDocument();
  });

  it("只有自评时说明 Dotty 仍在判断", () => {
    render(<MistakeAttribution attribution={{ selfAssessment: "reading" }} />);
    expect(screen.getByText("你的自评：审题错误。Dotty 还在判断中，等有更多证据再一起看看。")).toBeInTheDocument();
    expect(screen.queryByText(/Dotty 判断是/)).not.toBeInTheDocument();
  });

  it("只有 AI 判断时只显示 AI 归因", () => {
    render(<MistakeAttribution attribution={{ aiAssessment: "concept" }} />);
    expect(screen.getByText("Dotty 判断：概念不清")).toBeInTheDocument();
    expect(screen.queryByText(/自评|你觉得|一致/)).not.toBeInTheDocument();
  });

  it("两者都没有时不渲染区块", () => {
    const { container } = render(<MistakeAttribution attribution={{} satisfies MistakeAttributionData} />);
    expect(container).toBeEmptyDOMElement();
  });
});
