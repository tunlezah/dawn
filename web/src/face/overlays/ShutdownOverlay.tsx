export function ShutdownOverlay({ seconds }: { seconds: number }) {
  return (
    <div className="face-overlay" style={{ background: 'rgba(0,0,0,0.85)' }}>
      <div className="text-center">
        <div className="text-[6vmin] text-muted">Shutting down in</div>
        <div className="face-time big tnum">{seconds}</div>
        <div className="text-[3.6vmin] text-faint mt-[2vmin]">release the button to cancel</div>
      </div>
    </div>
  );
}
