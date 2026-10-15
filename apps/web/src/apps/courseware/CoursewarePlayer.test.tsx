// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { CoursewarePlayer, CoursewarePrintDocument } from "./CoursewarePlayer";
import type { CoursewarePresentationDocument } from "./coursewarePresentation";

const lesson: CoursewarePresentationDocument = {
  coursewareId: "course-1",
  publicationId: "publication-1",
  version: 1,
  title: "A source-based lesson",
  subject: "english",
  sourceUrl: "https://openstax.org/books/english",
  attributionText: "Access for free at openstax.org.",
  license: "CC BY-NC-SA 4.0",
  distributionScope: "classroom",
  durationMinutes: 40,
  sourceTitle: "Teacher-selected textbook",
  sections: [
    { sectionId: "warmup", title: "Warm-up", purpose: "Recall", estimatedMinutes: 5, pageIds: ["cover", "warmup-page"] },
    { sectionId: "practice", title: "Practice", purpose: "Try it", estimatedMinutes: 7, pageIds: ["practice-page"] },
  ],
  pages: [
    {
      pageId: "cover", sectionId: "warmup", pageKind: "cover", layout: "hero_split", title: "A source-based lesson",
      objectiveIds: [], sourceRefs: [], blocks: [{ kind: "text", blockId: "cover-intro", text: "Start with the selected textbook section.", sourceRefs: [] }],
      teacherNotes: "Introduce the selected source range.", revealSteps: [], estimatedMinutes: 1,
    },
    {
      pageId: "warmup-page", sectionId: "warmup", pageKind: "activity", layout: "two_column", title: "Notice and write",
      objectiveIds: ["objective-1"], sourceRefs: [{ sourceRevisionId: "source-1", page: 12 }],
      blocks: [
        { kind: "input", blockId: "input-observations", prompt: "Write three observations before you talk.", minResponses: 3, placeholder: "Your observation" },
        { kind: "checklist", blockId: "partner-check", title: "Partner check", items: [{ itemId: "contributed", label: "Each person contributed" }, { itemId: "used-source", label: "We used the source" }] },
      ],
      teacherNotes: "Give pairs two quiet minutes before comparing notes.",
      revealSteps: [
        { revealId: "example-1", label: "First example", content: "A source-grounded example." },
        { revealId: "example-2", label: "Second example", content: "Another source-grounded example." },
      ],
      estimatedMinutes: 4,
    },
    {
      pageId: "practice-page", sectionId: "practice", pageKind: "practice", layout: "cards", title: "Optional check",
      objectiveIds: ["objective-1"], sourceRefs: [],
      blocks: [
        { kind: "activity", activityId: "activity-1", activityKind: "choice", instructions: "Choose the supported statement.", assessmentMode: "objective", choices: ["Supported", "Not supported"] },
        { kind: "activity", activityId: "reflection-1", activityKind: "short_answer", instructions: "Add a short response.", assessmentMode: "teacher_review", choices: [] },
      ],
      teacherNotes: "The class may skip this if time is short.", revealSteps: [], estimatedMinutes: 3,
    },
  ],
  assets: [],
  canRevealAnswers: true,
};

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("CoursewarePlayer user flows", () => {
  it("user Given a saved server page When opening the lesson Then starts at that page without submitting practice", () => {
    render(<CoursewarePlayer document={lesson} initialPageId="practice-page" />);
    expect(screen.getByRole("heading", { level: 1, name: "Optional check" })).toBeVisible();
  });

  it("user Given a function activity When changing parameters and explicitly submitting Then uses the existing text answer contract", async () => {
    const attempts: Array<{ activityId: string; response: unknown }> = [];
    const functionLesson: CoursewarePresentationDocument = { ...lesson, subject: "math", canRevealAnswers: false, pages: [{
      ...lesson.pages[2], blocks: [{ kind: "activity", activityId: "function-value", activityKind: "fill_blank",
        instructions: "设置 k=2、b=1，求 x=2 时的 y。", assessmentMode: "objective", choices: [],
        responseSchema: { interaction: "linear_function", slope: 1, intercept: 0, sampleX: 2 } }],
    }] };
    const view = render(<CoursewarePlayer document={functionLesson} onSubmitActivity={async (activityId, response) => {
      attempts.push({ activityId, response }); return { assessment: "correct", feedback: "已记录" };
    }} />);
    fireEvent.change(screen.getByLabelText("斜率 k"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("截距 b"), { target: { value: "1" } });
    expect(screen.getByText("x = 2 时，y = 5")).toBeVisible();
    expect(attempts).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "把当前函数值填入作答" }));
    expect(screen.getByRole("textbox", { name: "Activity response" })).toHaveValue("5");
    view.unmount();
    render(<CoursewarePlayer document={functionLesson} onSubmitActivity={async (activityId, response) => {
      attempts.push({ activityId, response }); return { assessment: "correct", feedback: "已记录" };
    }} />);
    expect(screen.getByLabelText("斜率 k")).toHaveValue("2");
    fireEvent.click(screen.getByRole("button", { name: /Submit practice/ }));
    expect(await screen.findByText("correct · 已记录")).toBeVisible();
    expect(attempts).toEqual([{ activityId: "function-value", response: { text: "5" } }]);
    fireEvent.click(screen.getByRole("button", { name: "只看静态说明" }));
    expect(screen.getByRole("table", { name: "函数取值表" })).toBeVisible();
    expect(screen.queryByRole("application", { name: "交互数学画布" })).not.toBeInTheDocument();
  });

  it("user Given teacher content When printing the student edition Then all pages retain sources but omit notes and answers", () => {
    render(<CoursewarePrintDocument document={lesson} includeAnswers={false} />);
    expect(screen.getAllByRole("article", { name: /打印页/ })).toHaveLength(3);
    expect(screen.getByText("Start with the selected textbook section.")).toBeVisible();
    expect(screen.queryByText("Introduce the selected source range.")).not.toBeInTheDocument();
    expect(screen.queryByText("A source-grounded example.")).not.toBeInTheDocument();
    expect(screen.getAllByText(/Access for free at openstax.org/)).toHaveLength(3);
  });
  it("user_writes_checks_and_submits_a_cover_activity_then_restores_the_response_after_remount", async () => {
    // Given a cover page with image, observation, peer check and optional discussion
    const user = userEvent.setup();
    const submitted: string[] = [];
    const fixture: CoursewarePresentationDocument = { ...lesson, canRevealAnswers: false, pages: [{
      ...lesson.pages[0], blocks: [
        { kind: "image", blockId: "cover-image", assetId: "page-preview", alt: "Original source page" },
        { kind: "input", blockId: "cover-write", prompt: "Write first", minResponses: 1, placeholder: "Your clue" },
        { kind: "checklist", blockId: "cover-check", title: "Peer check", items: [{ itemId: "evidence", label: "Cites evidence" }] },
        { kind: "activity", activityId: "cover-discussion", activityKind: "discussion", instructions: "Explain a clue", assessmentMode: "teacher_review", choices: [] },
      ],
    }] };
    const submit = async (_id: string, response: { text?: string }) => {
      submitted.push(response.text ?? "");
      return { assessment: "needs_review", feedback: "Teacher review required." };
    };
    const view = render(<CoursewarePlayer document={fixture} onSubmitActivity={submit} />);
    // When the learner interacts with the first page
    await user.type(screen.getByRole("textbox", { name: "Observation 1" }), "Visible clue");
    await user.click(screen.getByRole("checkbox", { name: "Cites evidence" }));
    await user.type(screen.getByRole("textbox", { name: "Activity response" }), "The caption supports my interpretation.");
    await user.click(screen.getByRole("button", { name: "Submit practice: Explain a clue" }));
    expect(submitted).toEqual(["The caption supports my interpretation."]);
    expect(screen.getByRole("status")).toHaveTextContent("needs_review");
    view.unmount();
    // Then all local responses survive a remount of the same publication and scope
    render(<CoursewarePlayer document={fixture} onSubmitActivity={submit} />);
    expect(screen.getByRole("textbox", { name: "Observation 1" })).toHaveValue("Visible clue");
    expect(screen.getByRole("checkbox", { name: "Cites evidence" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: "Activity response" })).toHaveValue("The caption supports my interpretation.");
  });

  it("user_selects_several_choices_and_types_a_blank_using_the_published_interaction_mode", async () => {
    // Given a learner-safe page with explicit multiple selection and one blank
    const user = userEvent.setup();
    const responses: Array<{ text?: string; selectedOptions?: string[] }> = [];
    const submit = async (_id: string, response: { text?: string; selectedOptions?: string[] }) => {
      responses.push(response);
      return { assessment: "correct", feedback: "Saved." };
    };
    const sourcePage = lesson.pages[2];
    const fixture: CoursewarePresentationDocument = { ...lesson, canRevealAnswers: false, pages: [{
      ...sourcePage, blocks: [
        { kind: "activity", activityId: "multi", activityKind: "choice", instructions: "Choose both.", assessmentMode: "objective", choices: ["A", "B", "C"], selectionMode: "multiple" },
        { kind: "activity", activityId: "blank", activityKind: "fill_blank", instructions: "Name the audience.", assessmentMode: "objective", choices: [] },
      ],
    }] };
    render(<CoursewarePlayer document={fixture} onSubmitActivity={submit} />);
    // When the learner selects and toggles options, then submits the whole selection
    await user.click(screen.getByRole("button", { name: "A" }));
    await user.click(screen.getByRole("button", { name: "B" }));
    await user.click(screen.getByRole("button", { name: "C" }));
    await user.click(screen.getByRole("button", { name: "C" }));
    await user.click(screen.getByRole("button", { name: "Submit practice: Choose both." }));
    expect(responses[0]).toEqual({ selectedOptions: ["A", "B"] });
    // Then a blank uses the text field accepted by the server
    await user.type(screen.getByRole("textbox", { name: "Activity response" }), "audience");
    await user.click(screen.getByRole("button", { name: "Submit practice: Name the audience." }));
    expect(responses[1]).toEqual({ text: "audience" });
    expect(screen.getByRole("status")).toHaveTextContent("correct · Saved.");
  });
  it("user_restores classroom responses and revealed examples by publication", async () => {
    // Given a teacher presentation and an empty classroom session
    const user = userEvent.setup();
    const first = render(<CoursewarePlayer document={lesson} />);

    // When the teacher collects responses, checks partner work, and reveals examples one at a time
    await user.click(screen.getByRole("button", { name: "Go to page 2" }));
    const responses = screen.getAllByRole("textbox", { name: /Observation/ });
    await user.type(responses[0], "Look at the source");
    await user.type(responses[1], "Find a detail");
    expect(screen.getByText("2 / 3 written")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "Each person contributed" }));
    await user.click(screen.getByRole("tab", { name: "Answers" }));
    expect(screen.getByText("First example")).toBeInTheDocument();
    expect(screen.queryByText("A source-grounded example.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reveal First example" }));
    expect(screen.getByText("A source-grounded example.")).toBeInTheDocument();
    expect(screen.queryByText("Another source-grounded example.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reveal Second example" }));
    expect(screen.getByText("Another source-grounded example.")).toBeInTheDocument();

    // Then the same publication resumes that session, while a new publication starts clean
    first.unmount();
    const resumed = render(<CoursewarePlayer document={lesson} />);
    await user.click(screen.getByRole("button", { name: "Go to page 2" }));
    await user.click(screen.getByRole("tab", { name: "Page" }));
    expect(screen.getAllByRole("textbox", { name: /Observation/ })[0]).toHaveValue("Look at the source");
    expect(screen.getByRole("checkbox", { name: "Each person contributed" })).toBeChecked();
    await user.click(screen.getByRole("tab", { name: "Answers" }));
    expect(screen.getByText("A source-grounded example.")).toBeInTheDocument();
    expect(screen.getByText("Another source-grounded example.")).toBeInTheDocument();

    resumed.unmount();
    const secondPublication = render(<CoursewarePlayer document={{ ...lesson, publicationId: "publication-2" }} />);
    await user.click(within(secondPublication.container).getByRole("button", { name: "Go to page 2" }));
    const newPublicationInputs = within(secondPublication.container).getAllByRole("textbox", { name: /Observation/ });
    expect(newPublicationInputs[0]).toHaveValue("");
  });

  it("user_keeps_keyboard_shortcuts_out_of_answer_fields_and_opens_notes_separately", async () => {
    const user = userEvent.setup();
    render(<CoursewarePlayer document={lesson} />);
    await user.click(screen.getByRole("button", { name: "Go to page 2" }));
    const firstResponse = screen.getAllByRole("textbox", { name: /Observation/ })[0];
    firstResponse.focus();
    fireEvent.keyDown(firstResponse, { key: "ArrowRight" });
    fireEvent.keyDown(firstResponse, { key: "r" });
    expect(screen.getByRole("heading", { name: "Notice and write" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Teacher notes" }));
    expect(screen.getByText("Give pairs two quiet minutes before comparing notes.")).toBeInTheDocument();
    expect(screen.queryByText("Write three observations before you talk.")).not.toBeInTheDocument();
  });

  it("user_opens_teacher_notes_from_the_cover_without_changing_the_cover_image", async () => {
    const user = userEvent.setup();
    render(<CoursewarePlayer document={lesson} />);
    await user.click(screen.getByRole("button", { name: "Teacher notes" }));
    expect(screen.getByRole("tabpanel", { name: "Teacher notes" })).toHaveTextContent("Introduce the selected source range.");
    expect(screen.getByRole("heading", { name: "A source-based lesson" })).toBeInTheDocument();
  });

  it("user_sees_optional_practice_without_teacher_notes_or_answers_in_student_projection", async () => {
    const user = userEvent.setup();
    render(<CoursewarePlayer document={{ ...lesson, canRevealAnswers: false }} playbackScope="student:student-a" />);
    expect(screen.queryByRole("tab", { name: "Answers" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Teacher notes" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Go to page 2" }));
    fireEvent.keyDown(window, { key: "m" });
    expect(screen.queryByText("Give pairs two quiet minutes before comparing notes.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Practice · 7 min" }));
    expect(screen.getByText("Choose the supported statement.")).toBeInTheDocument();
    expect(screen.queryByText("A source-grounded example.")).not.toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Class timeline" })).getByText("Warm-up")).toBeInTheDocument();
  });

  it("user_changes_learner_scope_without_restoring_another_learners_classroom_input", async () => {
    const user = userEvent.setup();
    const view = render(<CoursewarePlayer document={lesson} playbackScope="student:learner-one" />);
    await user.click(screen.getByRole("button", { name: "Go to page 2" }));
    await user.type(screen.getAllByRole("textbox", { name: /Observation/ })[0], "Learner one's response");

    view.rerender(<CoursewarePlayer document={lesson} playbackScope="student:learner-two" />);
    await user.click(screen.getByRole("button", { name: "Go to page 2" }));
    expect(screen.getAllByRole("textbox", { name: /Observation/ })[0]).toHaveValue("");
    await user.type(screen.getAllByRole("textbox", { name: /Observation/ })[0], "Learner two's response");

    view.rerender(<CoursewarePlayer document={lesson} playbackScope="teacher:local" />);
    await user.click(screen.getByRole("button", { name: "Go to page 2" }));
    expect(screen.getAllByRole("textbox", { name: /Observation/ })[0]).toHaveValue("");
  });

  it("user_sees_source_attribution_and_license_on_the_cover", () => {
    render(<CoursewarePlayer document={lesson} />);
    expect(screen.getByRole("link", { name: "Teacher-selected textbook" })).toHaveAttribute("href", "https://openstax.org/books/english");
    expect(screen.getByText(/Access for free at openstax\.org\./)).toBeVisible();
    expect(screen.getByText(/CC BY-NC-SA 4\.0/)).toBeVisible();
    expect(screen.getByText(/Distribution: Classroom/)).toBeVisible();
  });

  it("user_submits_optional_practice_and_receives_feedback_without_answer_data", async () => {
    const user = userEvent.setup();
    const submit = vi.fn(async () => ({ assessment: "needs_review", feedback: "Saved for teacher review." }));
    render(<CoursewarePlayer document={{ ...lesson, canRevealAnswers: false, pages: lesson.pages.map(({ teacherNotes: _notes, revealSteps: _steps, ...page }) => page) }} onSubmitActivity={submit} />);
    await user.click(screen.getByRole("button", { name: "Practice · 7 min" }));
    await user.click(screen.getByRole("button", { name: "Supported" }));
    await user.click(screen.getByRole("button", { name: "Submit practice: Choose the supported statement." }));
    expect(submit).toHaveBeenCalledWith("activity-1", { selectedOptions: ["Supported"] });
    expect(await screen.findByRole("status")).toHaveTextContent("needs_review · Saved for teacher review.");
    expect(screen.queryByText("correct answer")).not.toBeInTheDocument();
  });

  it("user_resets_only_the_current_page_without_erasing_the_next_page_activity", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<CoursewarePlayer document={lesson} />);
    await user.click(screen.getByRole("button", { name: "Go to page 2" }));
    await user.type(screen.getAllByRole("textbox", { name: /Observation/ })[0], "Keep this private note");
    await user.click(screen.getByRole("button", { name: "Next ›" }));
    await user.click(screen.getByRole("button", { name: "Supported" }));
    await user.type(screen.getByRole("textbox", { name: "Activity response" }), "A short answer");
    await user.click(screen.getByRole("button", { name: /Prev/ }));
    await user.click(screen.getByText("Reset"));
    await user.click(screen.getByRole("button", { name: "Reset this page" }));
    expect(confirm).toHaveBeenCalledWith("只清空当前页的输入、同伴检查和答案揭晓状态吗？");
    expect(screen.getAllByRole("textbox", { name: /Observation/ })[0]).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Next ›" }));
    expect(screen.getByRole("button", { name: "Supported" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("textbox", { name: "Activity response" })).toHaveValue("A short answer");
  });
});
