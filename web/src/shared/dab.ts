// DAB Band III channel centre frequencies (MHz), ETSI EN 300 401 / Australian allocations.
const BAND_III: Record<string, number> = {
  '5A': 174.928, '5B': 176.64, '5C': 178.352, '5D': 180.064,
  '6A': 181.936, '6B': 183.648, '6C': 185.36, '6D': 187.072,
  '7A': 188.928, '7B': 190.64, '7C': 192.352, '7D': 194.064,
  '8A': 195.936, '8B': 197.648, '8C': 199.36, '8D': 201.072,
  '9A': 202.928, '9B': 204.64, '9C': 206.352, '9D': 208.064,
  '10A': 209.936, '10B': 211.648, '10C': 213.36, '10D': 215.072,
  '11A': 216.928, '11B': 218.64, '11C': 220.352, '11D': 222.064,
  '12A': 223.936, '12B': 225.648, '12C': 227.36, '12D': 229.072,
  '13A': 230.784, '13B': 232.496, '13C': 234.208, '13D': 235.776, '13E': 237.488, '13F': 239.2,
};

/** Band III channel names in frequency order. */
export const DAB_CHANNELS = Object.keys(BAND_III);

export function channelFrequency(channel: string | null): string | null {
  if (!channel) return null;
  const f = BAND_III[channel.toUpperCase()];
  return f ? `${f.toFixed(3)} MHz` : null;
}
