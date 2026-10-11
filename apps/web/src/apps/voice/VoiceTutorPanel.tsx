import { useVoiceTutor, type VoiceTutorPanelProps } from "./useVoiceTutor";
import "./voice.css";

export function VoiceTutorPanel(props: VoiceTutorPanelProps) {
  const { blockTitle } = props;
  const {
    session, recordingUrl, recordingBlob, recording, hasBrowserRecognition, audioRef, recordingSupported,
    startRecording, stopRecording, busy, speaking, stopTutorSpeech, recognizing, transcribeInBrowser,
    transcribeWithServer, selectAudioFile, transcript, setTranscript,
    setTranscriptSource, setBrowserConfidence, browserConfidence, savedDraft, savedConfidence,
    error, setError, notice, confirmAndRespond, cancelResponse, pendingGeneration,
  } = useVoiceTutor(props);
  return (
    <section className="voice-tutor panel" aria-label="当前步骤语音追问">
      <div className="voice-tutor-heading">
        <div>
          <span className="eyebrow">语音 Tutor · 当前步骤</span>
          <h2>{blockTitle}</h2>
        </div>
        <span className="voice-tutor-context">{session?.contextLabel || "正在恢复会话…"}</span>
      </div>

      {session?.turns.length ? (
        <ol className="voice-tutor-history" aria-label="本步骤语音对话">
          {session.turns.map((turn) => (
            <li key={turn.turnId}>
              <p><b>你确认的转写</b><span>{turn.transcript}</span></p>
              <p><b>导师追问</b><span>{turn.response}</span></p>
            </li>
          ))}
        </ol>
      ) : <p className="voice-tutor-empty">录制或输入你对这一步的疑问，确认后导师会围绕这一步继续追问。</p>}

      {recordingUrl && <audio ref={audioRef} className="voice-tutor-audio" controls src={recordingUrl} aria-label="回放录音" />}
      <div className="voice-tutor-controls">
        {recording
          ? <button type="button" className="danger" onClick={stopRecording}>停止录音</button>
          : <button type="button" onClick={() => void startRecording()} disabled={busy || !recordingSupported}>录制一段语音</button>}
        {recordingBlob && hasBrowserRecognition && <button type="button" onClick={() => void transcribeInBrowser()} disabled={busy || recognizing}>{recognizing ? "浏览器正在转写…" : "用浏览器转写这段录音"}</button>}
        {recordingBlob && session?.transcription.available && <button type="button" onClick={() => void transcribeWithServer()} disabled={busy}>用服务端转写</button>}
        <label className="voice-tutor-upload">上传录音
          <input type="file" accept="audio/*,.webm,.m4a,.mp3,.wav,.ogg,.flac" onChange={(event) => selectAudioFile(event.currentTarget.files?.[0])} disabled={busy} />
        </label>
      </div>

      {!recordingSupported && <p className="voice-tutor-note">浏览器录音不可用。可上传现有录音，或直接输入转写文字。</p>}
      {!session?.transcription.available && <p className="voice-tutor-note">{session?.transcription.detail || "转写服务尚未配置"}。浏览器转写能力也可能因浏览器而异。</p>}
      {hasBrowserRecognition && <p className="voice-tutor-note">浏览器转写由浏览器提供，可能联网处理；它提供的置信值仅作参考。</p>}

      <label className="voice-tutor-transcript">
        <span>转写草稿，请检查后确认</span>
        <textarea
          value={transcript}
          onChange={(event) => {
            setTranscript(event.target.value);
            setTranscriptSource("manual");
            setBrowserConfidence(null);
            setError("");
          }}
          maxLength={5000}
          rows={3}
          placeholder="转写结果会先显示在这里；也可以手动输入你刚才说的话。"
          disabled={!session || busy}
        />
      </label>
      {(savedConfidence || browserConfidence !== null) && <p className="voice-tutor-confidence">
        {savedConfidence?.kind === "token_logprobs"
          ? `模型转写参考：${String(savedConfidence.lowConfidenceTokenCount ?? 0)} 个低分词项，需人工确认。`
          : `浏览器转写参考置信值：${Math.round((browserConfidence ?? Number(savedConfidence?.score ?? 0)) * 100)}%，需人工确认。`}
      </p>}
      {savedDraft?.ambiguityFlags.length ? <p className="voice-tutor-confidence">需要复核：{savedDraft.ambiguityFlags.join("、")}</p> : null}
      {error && <p className="voice-tutor-error" role="alert">{error}</p>}
      {notice && <p className="voice-tutor-note" role="status">{notice}</p>}

      <div className="voice-tutor-footer">
        <span>{savedDraft?.needsConfirmation === false ? "转写已确认" : savedDraft ? "转写尚未确认" : session ? "等待转写或手动输入" : "会话恢复中"}</span>
        {busy && pendingGeneration !== null
          ? <button type="button" className="danger" onClick={() => void cancelResponse()}>打断导师回复</button>
          : speaking
            ? <button type="button" className="danger" onClick={stopTutorSpeech}>停止导师播报</button>
            : <button type="button" onClick={() => void confirmAndRespond()} disabled={!session || !transcript.trim() || busy}>
              {busy ? "正在处理…" : "确认转写并追问"}
            </button>}
      </div>
    </section>
  );
}
