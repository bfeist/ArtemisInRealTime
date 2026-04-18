import { JSX, useEffect, useState } from "react";
import styles from "./ComingSoon.module.css";

const SPLASHDOWN_DATE = new Date("2026-04-10T20:07:27-04:00");

interface TimeElapsed {
  days: number;
  hours: number;
  minutes: number;
  seconds: number;
}

function getTimeElapsed(): TimeElapsed {
  const now = new Date();
  const diff = Math.max(0, now.getTime() - SPLASHDOWN_DATE.getTime());
  const totalSeconds = Math.floor(diff / 1000);

  return {
    days: Math.floor(totalSeconds / 86400),
    hours: Math.floor((totalSeconds % 86400) / 3600),
    minutes: Math.floor((totalSeconds % 3600) / 60),
    seconds: totalSeconds % 60,
  };
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function ComingSoon(): JSX.Element {
  const [time, setTime] = useState<TimeElapsed>(getTimeElapsed);

  useEffect(() => {
    const interval = setInterval(() => {
      setTime(getTimeElapsed());
    }, 1000);
    return (): void => {
      clearInterval(interval);
    };
  }, []);

  return (
    <div className={styles.pageContent}>
      <div className={styles.comingSoon}>Coming Soon</div>
      <div className={styles.timerHeader}>Time Since Artemis II Splashdown</div>
      <div className={styles.countdown}>
        <div className={styles.countdownSegment}>
          <div className={styles.countdownValue}>{pad(time.days)}</div>
          <div className={styles.countdownLabel}>Days</div>
        </div>
        <div className={styles.separator}>:</div>
        <div className={styles.countdownSegment}>
          <div className={styles.countdownValue}>{pad(time.hours)}</div>
          <div className={styles.countdownLabel}>Hours</div>
        </div>
        <div className={styles.separator}>:</div>
        <div className={styles.countdownSegment}>
          <div className={styles.countdownValue}>{pad(time.minutes)}</div>
          <div className={styles.countdownLabel}>Minutes</div>
        </div>
        <div className={styles.separator}>:</div>
        <div className={styles.countdownSegment}>
          <div className={styles.countdownValue}>{pad(time.seconds)}</div>
          <div className={styles.countdownLabel}>Seconds</div>
        </div>
      </div>
    </div>
  );
}

export default ComingSoon;
