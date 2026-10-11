// Reusable wrapper around Azure Pronunciation Assessment for the browser.
// This is the file to lift into Epistemy's speech-test. It needs only:
//   1. the Speech SDK bundle loaded as window.SpeechSDK
//   2. a getToken() function returning { token, region } from YOUR backend
//
// Usage:
//   const a = createAssessor({ getToken, locale: "en-US", onInterim, onPhrase, onError });
//   await a.start();
//   const result = await a.stop();   // aggregate() output: scores, metrics, words, pauses

import { parsePhrase, aggregate } from "./scoring.js";

export function createAssessor({
  getToken,
  locale = "en-US",
  referenceText = "", // empty = unscripted (open answer). Set it for read-aloud tasks.
  onInterim = () => {},
  onPhrase = () => {},
  onError = () => {},
} = {}) {
  const SDK = globalThis.SpeechSDK;
  if (!SDK) throw new Error("Speech SDK not loaded (expected window.SpeechSDK).");

  let recognizer = null;
  let phrases = [];
  let stopped = null; // resolves when the SDK session has fully stopped

  async function start() {
    phrases = [];
    const { token, region } = await getToken();

    const speechConfig = SDK.SpeechConfig.fromAuthorizationToken(token, region);
    speechConfig.speechRecognitionLanguage = locale;
    speechConfig.outputFormat = SDK.OutputFormat.Detailed;

    const scripted = referenceText.trim().length > 0;
    const pronConfig = new SDK.PronunciationAssessmentConfig(
      referenceText,
      SDK.PronunciationAssessmentGradingSystem.HundredMark,
      SDK.PronunciationAssessmentGranularity.Phoneme,
      scripted, // miscue detection only makes sense against a reference
    );
    pronConfig.enableProsodyAssessment = true;

    const audioConfig = SDK.AudioConfig.fromDefaultMicrophoneInput();
    recognizer = new SDK.SpeechRecognizer(speechConfig, audioConfig);
    pronConfig.applyTo(recognizer);

    stopped = new Promise((resolve) => {
      recognizer.sessionStopped = () => resolve();
      recognizer.canceled = (_s, e) => {
        if (e.reason === SDK.CancellationReason.Error) onError(new Error(e.errorDetails || "Recognition canceled"));
        resolve();
      };
    });

    recognizer.recognizing = (_s, e) => onInterim(e.result.text);
    recognizer.recognized = (_s, e) => {
      if (e.result.reason !== SDK.ResultReason.RecognizedSpeech) return;
      const raw = e.result.properties.getProperty(SDK.PropertyId.SpeechServiceResponse_JsonResult);
      let phrase = null;
      try { phrase = parsePhrase(JSON.parse(raw)); } catch { /* malformed JSON: skip this phrase */ }
      if (phrase) {
        phrases.push(phrase);
        onPhrase(phrase);
      }
    };

    await new Promise((resolve, reject) => recognizer.startContinuousRecognitionAsync(resolve, reject));
  }

  async function stop() {
    if (!recognizer) return aggregate(phrases);
    await new Promise((resolve) => recognizer.stopContinuousRecognitionAsync(resolve, resolve));
    await Promise.race([stopped, new Promise((r) => setTimeout(r, 3000))]); // flush last phrase
    recognizer.close();
    recognizer = null;
    return aggregate(phrases);
  }

  return { start, stop };
}
