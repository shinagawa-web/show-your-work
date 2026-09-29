export const FEATURE_VERSION = import.meta.env.VITE_APP_VERSION;
export function render() {
  return `feature from ${FEATURE_VERSION}`;
}
