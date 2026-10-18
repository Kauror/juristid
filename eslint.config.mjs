// The JavaScript check CI runs over static/js (FIX-PLAN F8, JARCH-004).
//
// CI only. The application has no Node build step and gets none from this: the
// scripts are served exactly as written, and this file is read by the `quality`
// job's pinned ESLint (.github/workflows/ci.yml) and by nothing at runtime.
//
// Two rules on purpose. `no-undef` catches a misspelt name and a browser API
// used without being listed below, either of which stops every behaviour
// registered after it in a one-function script (docs/adr/0171). `no-unused-vars`
// catches what a refactor left behind. Style is not checked.
//
// The globals are listed by hand rather than taken from a package, so the list
// is the reviewed set of browser APIs the layer uses: a new one is a deliberate
// line here, not a silent dependency.
const browser = {
  AbortController: "readonly",
  CSS: "readonly",
  CustomEvent: "readonly",
  DOMParser: "readonly",
  DataTransfer: "readonly",
  Event: "readonly",
  FormData: "readonly",
  MutationObserver: "readonly",
  Node: "readonly",
  URL: "readonly",
  URLSearchParams: "readonly",
  document: "readonly",
  fetch: "readonly",
  navigator: "readonly",
  window: "readonly",
};

export default [
  {
    files: ["static/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: browser,
    },
    linterOptions: {
      reportUnusedDisableDirectives: "error",
    },
    rules: {
      "no-undef": "error",
      // A `catch (error)` that does not read the error is how these files say
      // "nothing to do"; optional catch bindings are not used here.
      "no-unused-vars": ["error", { caughtErrors: "none" }],
    },
  },
];
