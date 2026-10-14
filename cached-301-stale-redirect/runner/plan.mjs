export const DOCS = 'http://localhost:18080';
export const DOCS_B4 = 'http://localhost:18081';
export const LINKS_URL = 'http://127.0.0.1:18080/';
export const WAIT_AFTER_SWITCH_SEC = 70;
export const IMAGE = 'cached-301-site:latest';
export const CONTAINER = 'cached-301-site';

export const CONDITIONS = [
  { id: 'b0', entrance: `${DOCS}/b0/docs`, profile: 'main', afterSwitch: 'link' },
  { id: 'b1', entrance: `${DOCS}/b1/docs`, profile: 'main', afterSwitch: 'link' },
  { id: 'b2', entrance: `${DOCS}/b2/docs`, profile: 'main', afterSwitch: 'link' },
  { id: 'b3', entrance: `${DOCS}/b3/docs`, profile: 'main', afterSwitch: 'link' },
  { id: 'b4', entrance: `${DOCS_B4}/b4/docs`, profile: 'b4', afterSwitch: 'link', clearSiteDataPage: `${DOCS_B4}/docs/v4/` },
  { id: 'b5', entrance: `${DOCS}/b5/docs`, profile: 'main', afterSwitch: 'goto' },
  { id: 'b6', entrance: `${DOCS}/b6/docs`, profile: 'main', afterSwitch: 'link' },
];

export const CURL_URLS = [
  ...CONDITIONS.map((c) => c.entrance),
  `${DOCS}/docs/v3/`,
  `${DOCS}/docs/v4/`,
  `${DOCS_B4}/docs/v4/`,
  LINKS_URL,
];
