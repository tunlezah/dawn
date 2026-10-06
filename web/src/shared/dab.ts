// DAB Band III channel centre frequencies (MHz), ETSI EN 300 401 / Australian allocations.
const BAND_III: Record<string, number> = {
  '5A': 174.928, '5B': 176.64, '5C': 178.352, '5D': 180.064,
  '6A': 181.936, '6B': 183.648, '6C': 185.36, '6D': 187.072,
  '7A': 188.928, '7B': 190.64, '7C': 192.352, '7D': 194.064,
  '8A': 196.928, '8B': 198.64, '8C': 200.352, '8D': 202.064,
  '9A': 204.928, '9B': 206.64, '9C': 208.352, '9D': 210.064,
  '10A': 211.648, '10B': 213.36, '10C': 215.072, '10D': 216.928,
  '11A': 218.64, '11B': 220.352, '11C': 222.064, '11D': 223.936,
  '12A': 225.648, '12B': 227.36, '12C': 229.072, '12D': 230.784,
  '13A': 232.496, '13B': 234.208, '13C': 235.776, '13D': 237.488, '13E': 239.2, '13F': 240.8,
};

/** Band III channel names in frequency order. */
export const DAB_CHANNELS = Object.keys(BAND_III);

export function channelFrequency(channel: string | null): string | null {
  if (!channel) return null;
  const f = BAND_III[channel.toUpperCase()];
  return f ? `${f.toFixed(3)} MHz` : null;
}
