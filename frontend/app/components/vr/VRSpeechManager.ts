/**
 * Browser-native Web Speech API Manager for MeshWorks VR.
 * Handles in-VR microphone speech recognition and spatial speech synthesis (TTS).
 */

declare global {
  interface Window {
    SpeechRecognition?: any;
    webkitSpeechRecognition?: any;
  }
}

export class VRSpeechManager {
  private recognition: any = null;
  private isListening = false;
  private onTranscriptCallback: ((text: string) => void) | null = null;
  private onStatusChangeCallback: ((status: string) => void) | null = null;

  constructor() {
    if (typeof window !== "undefined") {
      const SpeechRecognition =
        window.SpeechRecognition || window.webkitSpeechRecognition;
      if (SpeechRecognition) {
        this.recognition = new SpeechRecognition();
        this.recognition.continuous = false;
        this.recognition.interimResults = false;
        this.recognition.lang = "en-US";

        this.recognition.onstart = () => {
          this.isListening = true;
          this.notifyStatus("Listening... Speak your question");
        };

        this.recognition.onresult = (event: any) => {
          const transcript = event.results[0]?.[0]?.transcript || "";
          if (transcript.trim() && this.onTranscriptCallback) {
            this.onTranscriptCallback(transcript.trim());
          }
        };

        this.recognition.onerror = (event: any) => {
          console.warn("[VRSpeech] Recognition error:", event.error);
          this.isListening = false;
          this.notifyStatus(`Mic error: ${event.error || "unrecognized"}`);
        };

        this.recognition.onend = () => {
          this.isListening = false;
          this.notifyStatus("Mic idle (Click to speak)");
        };
      } else {
        console.warn("[VRSpeech] Web Speech API not supported in this browser.");
      }
    }
  }

  public isSupported(): boolean {
    return Boolean(this.recognition);
  }

  public getIsListening(): boolean {
    return this.isListening;
  }

  public onTranscript(callback: (text: string) => void) {
    this.onTranscriptCallback = callback;
  }

  public onStatusChange(callback: (status: string) => void) {
    this.onStatusChangeCallback = callback;
  }

  private notifyStatus(status: string) {
    if (this.onStatusChangeCallback) {
      this.onStatusChangeCallback(status);
    }
  }

  public toggleListening() {
    if (!this.recognition) {
      this.notifyStatus("Speech API unavailable on this browser");
      return;
    }

    if (this.isListening) {
      this.recognition.stop();
      this.isListening = false;
    } else {
      try {
        this.recognition.start();
      } catch (err) {
        console.warn("[VRSpeech] Could not start speech recognition:", err);
      }
    }
  }

  /**
   * Speak aloud using browser's SpeechSynthesis engine.
   */
  public speak(text: string, onEnd?: () => void) {
    if (typeof window === "undefined" || !window.speechSynthesis) {
      if (onEnd) onEnd();
      return;
    }

    try {
      window.speechSynthesis.cancel(); // cancel any active utterance
      const cleanText = text.replace(/[*#_`]/g, ""); // strip markdown formatting
      const utterance = new SpeechSynthesisUtterance(cleanText);
      utterance.rate = 1.0;
      utterance.pitch = 1.05;
      utterance.lang = "en-US";

      if (onEnd) {
        utterance.onend = () => onEnd();
        utterance.onerror = () => onEnd();
      }

      window.speechSynthesis.speak(utterance);
    } catch (err) {
      console.warn("[VRSpeech] SpeechSynthesis error:", err);
      if (onEnd) onEnd();
    }
  }

  public stopSpeaking() {
    if (typeof window !== "undefined" && window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
  }
}
