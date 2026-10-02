export function ShutdownOverlay({ seconds }: { seconds: number }) {
  return (
    <div className="face-overlay" style={{ background: 'rgba(0,0,0,0.85)', pointerEvents: 'auto' }}>
      <div className="text-center">
        <div className="f-sub">Shutting down in</div>
        <div className="face-time f-ambient-clock tnum">{seconds}</div>
        <div className="f-tiny mt-[2vmin]">release the button to cancel</div>
      </div>
    </div>
  );
}
