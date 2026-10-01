# Website advertising

The browser edition uses AdSense for website monetization. AdMob is a separate
mobile-app product. The publisher is `pub-9792422128121970`; the fixed 300 × 250
guide unit is `7269853953` (Flow Puzzle Solver — Guide — 300x250).

## Visitor experience

- `/guide/` is a separate, static informational document with one ad placement.
  It is beside the article on desktop and below the article on phones. Below
  340 CSS pixels the placement is hidden to avoid horizontal scrolling.
- Visitors explicitly select **Show optional ad** to load the Google tag. That
  choice is not persisted. Fresh visits make no Google advertising requests,
  so a consent dialog cannot unexpectedly interrupt reading or puzzle solving.
- Google's published European and US privacy messages are delivered through
  the AdSense tag. European messages offer consent, rejection and a close button;
  US messages provide an opt-out link for supported states. The Google consent
  API supplies the privacy-choice control once available.
- **Turn off ads** reloads the document without the tag or ad frames. It does
  not erase Google's saved privacy choices. Unavailable/blocked ads never gate
  content. No automatic refresh, rewarded ads, interstitials or offerwall.
- The solver opens the guide/privacy notice in a new tab to preserve an active
  board. Solver, editor, library, original in-app Help, and privacy notice carry
  no Google advertising runtime.

## Isolation and security

The solver needs COOP `same-origin` and COEP `require-corp` for shared-memory
WebAssembly. Its strict same-origin CSP remains unchanged. `/guide/*` removes
COEP and the inherited CSP; the standalone HTML supplies its own advertising
CSP and the response sets `X-Frame-Options: DENY`. `/privacy/*` removes COEP but
retains the strict CSP. These are full-document navigations, never SPA state
changes. Vite's development middleware mirrors the document separation.

Guide CSP permits Google's scripts and HTTPS ad frames, connections and images.
Do not copy this policy to the solver. The static build audit traverses the
solver's module graph and fails if advertising code is imported there.

## Publisher configuration and verification

- `frontend/public/ads.txt` contains Google's provided seller record.
- Static HTML receives Google's account-verification meta tag at build time;
  verification does not require loading advertising code on the solver.
- `frontend/src/publishing/publisherSettings.ts` contains the public identifiers and
  the kill switch. Production ad loading is restricted to `flowpuzzlesolver.net`.
- Auto ads and automatic ad placement/optimization should remain off. Do not
  enable anchor, vignette, intent, offerwall or ad-block recovery formats.
- Google must approve the site before it can serve ads. An empty slot during
  review is expected; neither a successful build nor account setup is approval.

## Validation

Run `npm test` and `npm run build:static` in `frontend`. Use
`npm run preview:static` to test the Cloudflare response headers. `/`, `/create`,
`/library` and `/docs` must remain isolated; `/guide/` must have no COEP and must
include its meta CSP. Verify `/ads.txt` is served as text, not the SPA fallback.

Use `/guide/?previewAd=1` on localhost for a labeled layout placeholder that
never requests an ad. This preview flag is ignored on the production domain.
Check desktop, 390-pixel phone and 320-pixel narrow layouts; the last must not
overflow. Solve the included demo after visiting the guide. Inspect fresh guide
and solver loads for third-party scripts. Never click live ads as a test.

Publisher setup changes are made in Google's console. Keep the privacy URL set
to `https://flowpuzzlesolver.net/privacy/`. Message publication can take up to an
hour to propagate; validate regional consent before broadening ad loading.
