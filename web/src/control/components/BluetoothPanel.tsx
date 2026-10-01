import { useState } from '../../shared/store';
import { Card, Row, Empty, Spinner } from '../../shared/components';
import { api } from '../../shared/api';

export function BluetoothPanel() {
  const s = useState();
  const bt = s.bluetooth;
  return (
    <Card title="Bluetooth" action={
      <div className="flex gap-2">
        {bt.available && <button className="btn btn-sm" onClick={() => api.post('/api/bluetooth/discoverable', { seconds: 180 })} disabled={bt.discoverable}>{bt.discoverable ? 'Discoverable…' : 'Pair new device'}</button>}
        <span className={`chip ${bt.available ? '' : 'opacity-60'}`}>{bt.available ? (bt.connected ? 'connected' : bt.powered ? 'on' : 'off') : 'unavailable'}</span>
      </div>}>
      {!bt.available && <Empty>Bluetooth is not available on this device.</Empty>}
      {bt.available && bt.discoverable && <div className="text-sm text-muted mb-2 flex items-center gap-2"><Spinner />Visible as <b className="text-fg">{bt.name}</b>. Pair from your phone; pairing is accepted automatically.</div>}
      {bt.devices.map((d) => (
        <Row key={d.address} label={d.name || d.address} hint={`${d.address}${d.connected ? ' · connected' : d.paired ? ' · paired' : ''}`}>
          {d.paired && !d.connected && <button className="btn btn-sm" onClick={() => api.post('/api/bluetooth/connect', { address: d.address })}>Connect</button>}
          {d.connected && <button className="btn btn-sm" onClick={() => api.post('/api/bluetooth/disconnect', { address: d.address })}>Disconnect</button>}
          {!d.paired && <button className="btn btn-sm" onClick={() => api.post('/api/bluetooth/pair', { address: d.address })}>Pair</button>}
          {d.paired && <button className="btn btn-sm btn-ghost" onClick={() => api.del(`/api/bluetooth/${d.address}`)}>Forget</button>}
        </Row>
      ))}
      {bt.available && bt.devices.length === 0 && <Empty>No paired devices.</Empty>}
    </Card>
  );
}
