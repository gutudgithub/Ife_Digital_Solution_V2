# Stage 12 native-language review

## Current gate

The Amharic (`am`) and Afaan Oromoo (`om`) source catalogs have been extracted from the
application. Their untranslated entries intentionally fall back to English. No machine
translation is accepted as product copy, and `STAGE12_TRANSLATION_REVIEW_APPROVED` must remain
false until named native speakers complete and sign this review.

## Reviewer record

For each language retain:

- reviewer name and language;
- review date;
- reviewed commit identifier;
- catalog checksum;
- corrections requested and accepted;
- confirmation that the browser, printed pages, QR alternatives, and error/status messages
  were reviewed in context.

## Mandatory in-context scenarios

1. Sign in, change language, sign out, and invalid-login/rate-limit messages.
2. Internal product cards, missing-image fallback, image upload validation, removal warning,
   selling price, and restricted reference-cost wording.
3. Visual sale picker search, size/color/unit labels, add/increment status, validation errors,
   and no-JavaScript line entry.
4. Telebirr setup, test-scan confirmation, activation, deactivation, removal, exact ETB amount,
   manual reference entry, and the warning that QR display/scanning does not confirm payment.
5. Staff-entry poster, sign-in requirement, visible HTTPS URL, print action, and QR alternative
   text.
6. Public profile and product cards, missing-image fallback, browsing-only disclaimer,
   indexing/cache-removal disclosure, receipt-verification limitations, and correction link.
7. Offline status, provisional-note disclaimers, draft ownership, expiry, conflicts,
   acknowledgement/discard warnings, and backdated cash-session disclosure.
8. Document upload, quarantine/scanning status, retention/destructive actions, transcription,
   owner confirmation, and source-authenticity limitations.
9. Performance formula names, omissions, CSV/print wording, operational-control exclusions,
   and all financial warnings.
10. Keyboard focus, skip link, live status, field errors, alternative text, 200% zoom,
    320-pixel reflow, and printed QR readability.

## Acceptance rules

- Preserve interpolation variables such as `%(total)s`, HTML-safe context, punctuation, and
  units.
- Prefer established Ethiopian retail and accounting terms; do not imply statutory profit,
  tax-invoice status, provider payment verification, guaranteed stock, source authenticity,
  unique visitors, or immediate search-engine cache removal.
- Destructive actions must state what remains auditable and what bytes or local records are
  removed.
- QR alternative text must identify purpose without embedding passwords, tokens, payment
  references, or private branch data.
- Review at narrow phone width and in print, not only in the catalog file.
- A second reviewer should inspect high-risk payment, financial, privacy, offline, and
  destructive-action wording when practical.

After corrections, regenerate binary catalogs with `python manage.py compilemessages`, run the
test suite under all three languages, record the evidence, and only then set the deployment
approval flag.
