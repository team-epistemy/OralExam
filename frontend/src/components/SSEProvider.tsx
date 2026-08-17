import { createContext, useContext, useEffect, useRef, useState, useCallback } from 'react';
import { connectToExamSSE } from '../api/exam';

interface SSEQuestion {
  question_id: string;
  text: string;
  topic?: string;
}

interface SSEContextValue {
  connected: boolean;
  currentQuestion: SSEQuestion | null;
  sessionComplete: boolean;
  feedback: string | null;
  connect: (sessionId: string) => void;
  disconnect: () => void;
}

const SSEContext = createContext<SSEContextValue>({
  connected: false,
  currentQuestion: null,
  sessionComplete: false,
  feedback: null,
  connect: () => {},
  disconnect: () => {},
});

export function SSEProvider({ children }: { children: React.ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [currentQuestion, setCurrentQuestion] = useState<SSEQuestion | null>(null);
  const [sessionComplete, setSessionComplete] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const disconnect = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setConnected(false);
  }, []);

  const connect = useCallback((sessionId: string) => {
    disconnect();
    const es = connectToExamSSE(sessionId);
    eventSourceRef.current = es;

    es.onopen = () => {
      setConnected(true);
    };

    es.addEventListener('question', (event) => {
      const question = JSON.parse(event.data) as SSEQuestion;
      setCurrentQuestion(question);
      setFeedback(null);
    });

    es.addEventListener('feedback', (event) => {
      const data = JSON.parse(event.data);
      setFeedback(data.feedback);
    });

    es.addEventListener('complete', () => {
      setSessionComplete(true);
      disconnect();
    });

    es.onerror = () => {
      setConnected(false);
    };
  }, [disconnect]);

  useEffect(() => {
    return () => {
      disconnect();
    };
  }, [disconnect]);

  return (
    <SSEContext.Provider value={{ connected, currentQuestion, sessionComplete, feedback, connect, disconnect }}>
      {children}
    </SSEContext.Provider>
  );
}

export function useSSE() {
  return useContext(SSEContext);
}
