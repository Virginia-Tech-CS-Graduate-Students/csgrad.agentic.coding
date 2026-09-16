---
name: clean-up-references
description: Clean, cross-link, validate, format, alphabetize, and audit scholarly citations and references in Microsoft Word DOCX files while preserving all non-citation document text. Use when a user wants numbered Word cross-references, ACM references, reference validation, URL verification, BibTeX output, or literature suggestions for a Word manuscript.
compatibility: Agent Skills open standard. Requires DOCX file access; a DOCX-capable editor/library able to create Word bookmarks and REF fields; document rendering/inspection for final QA; and web/network access for reference, URL, and literature validation goals.
metadata:
  version: "1.1.0"
---

# Clean Up References

## Purpose

Use this skill to improve citations and the References section of a Microsoft Word `.docx` scholarly document without rewriting, editing, polishing, or otherwise changing the paper's substantive text.

Run Goals G01 through G10 in order unless the user explicitly requests a subset. Treat each goal as transactional: perform the goal, validate it, repair structural or mapping failures, and only then continue.

## Non-negotiable invariants

1. **Do not change substantive document text.**
   - Only modify in-text citation spans/fields and content inside the References/Bibliography section.
   - Preserve headings, tables, figures, captions, equations, footnotes/endnotes, headers/footers, and other content except citation spans located inside them.
   - Preserve narrative author wording. Example: `Smith et al. (2024)` may become `Smith et al. [7]`; do not rewrite `Smith et al.`.

2. **Preserve source identity, not old numbers.**
   - Never renumber with blind search-and-replace.
   - Maintain a source-identity map from source -> stable bookmark -> current number -> citation locations.

3. **Use real Microsoft Word cross-reference fields.**
   - Final in-text citation numbers must be Word `REF` fields connected to stable bookmarks on numbered reference entries, not static text or ordinary hyperlinks.
   - Prefer behavior equivalent to literal `[` + `{ REF <bookmark> \n \h }` + literal `]` for a single-level numbered reference paragraph.
   - Keep fields live; do not flatten them.

4. **Cite multiple works separately.**
   - Render `[1], [2]`, not `[1, 2]`.
   - Expand compact groups/ranges when their mapping can be established safely.

5. **Never invent bibliographic facts.**
   - Correct metadata automatically only when authoritative evidence is strong.
   - Flag ambiguity as `UNRESOLVED` rather than guessing.

6. **Do not silently add or delete legitimate works during G01-G09.**
   - New literature belongs only in G10 recommendations unless the user separately approves adding it.
   - Do not silently delete uncited references; flag them.
   - Exact duplicates may be consolidated only when source identity is unambiguous and every citation is remapped correctly.

7. **Protect document integrity.**
   - Build a pre-edit fingerprint of non-citation content and compare it again in G08.
   - Render and inspect the final DOCX in addition to structural field validation.

8. **Deliver a warning-free Word open experience for fields created by this skill.**
   - Do not enable automatic field updates on document open merely to refresh citation numbers.
   - Do not introduce `<w:updateFields w:val="true"/>` in `word/settings.xml`. If the source document did not already contain an update-on-open setting, the final document must not add one.
   - Citation `REF` fields created by this skill must have correct cached display results before delivery and must not be left marked dirty solely to force Word to recalculate them at open.
   - Keep the `REF` fields live and manually updatable; do not lock or flatten them.
   - The skill must not introduce external-link field codes such as `LINK`, `INCLUDETEXT`, `INCLUDEPICTURE`, `DDE`, `DDEAUTO`, `DATABASE`, or `RD`. Normal clickable URL hyperlink relationships in the References section are allowed.
   - If opening the generated DOCX would cause Word to ask whether to update fields that may refer to other files, treat that as a failed G08 acceptance check and repair the package before delivery.

9. **Do not claim checks that were not actually performed.**
   - G04, G06, G07, and G10 require external verification/search.
   - If web access is unavailable, mark those goals incomplete rather than fabricating results.

## Inputs

Primary input:
- One Microsoft Word `.docx` document containing scholarly citations and a References/Bibliography section.

Optional inputs:
- Existing `.bib` / BibTeX file.
- DOI list, EndNote/Zotero/Mendeley export, or other metadata.
- User-supplied formatting requirements that explicitly override defaults in this skill.

If more than one candidate Word file is present and the intended source is not obvious, use the file most clearly identified by the user's request. Do not overwrite the original unless the user explicitly requests it.

## Required supporting instructions

Read the following files as needed. They are part of this skill and are authoritative extensions of this file:

- [Preflight, citation recognition, duplicates, and audit model](references/PREFLIGHT-AND-CITATIONS.md)
- [Goals G01-G03: Word cross-references, ACM formatting, alphabetization](references/GOALS-G01-G03.md)
- [Goals G04-G07: reference validation, canonical URLs, URL validation](references/GOALS-G04-G07.md)
- [Goals G08-G10: final integrity audit, outputs, literature recommendations](references/GOALS-G08-G10.md)
- [Failure handling, acceptance checklist, and final summary](references/FAILURE-AND-ACCEPTANCE.md)

## Execution sequence

### Preflight

Before editing:
1. Locate the true References/Bibliography section.
2. Inventory every reference and assign a stable internal `source_id` independent of its current number.
3. Inventory every citation in all reachable Word story content.
4. Resolve citations to source identities with confidence tracking.
5. Build the source-identity map.
6. Capture a non-citation content fingerprint and relevant document structure counts.

Follow the full preflight rules in `references/PREFLIGHT-AND-CITATIONS.md`.

### G01 — Convert citations to real Word cross-references

Convert safely resolvable numeric and author-year citations to real Word `REF` fields tied to stable bookmarks on automatic numbered bibliography entries. Support both parenthetical and narrative citations. Keep grouped works separate. Detect missing targets, uncited references, broken fields, and duplicates.

Do not continue until every safely resolvable citation still maps to the same source identity and the non-citation fingerprint remains unchanged.

Detailed procedure and gate: `references/GOALS-G01-G03.md`.

### G02 — Format references in ACM style

Format only the References section using ACM-oriented numbered-reference conventions and verified metadata. Do not change manuscript prose. Do not fabricate missing bibliographic information.

Detailed procedure and gate: `references/GOALS-G01-G03.md`.

### G03 — Alphabetize references and safely renumber

Sort the References section alphabetically by first author/organization with deterministic tie-breaks. Preserve stable source bookmarks so each in-text `REF` field continues to cite the correct work after automatic renumbering.

Detailed procedure and gate: `references/GOALS-G01-G03.md`.

### G04 — Validate every reference

Verify that each cited work actually exists and that identity-bearing metadata is correct. Use authoritative sources first. Automatically correct strongly supported errors only inside the References section. Record `VERIFIED`, `CORRECTED`, or `UNRESOLVED` for every entry.

Detailed hierarchy, correction policy, and gate: `references/GOALS-G04-G07.md`.

### G05 — Recheck alphabetical order

After metadata corrections, re-sort if corrected author/organization/year/title data changes the proper order. Revalidate all source-to-citation mappings after any movement.

Detailed procedure and gate: `references/GOALS-G04-G07.md`.

### G06 — Add one canonical URL to every resolvable reference

Append a visible URL to the end of every resolvable bibliography entry, including when ACM formatting might otherwise omit it. Prefer URLs in this order:
1. DOI URL (`https://doi.org/...`)
2. official publisher/proceedings page
3. arXiv/preprint page when appropriate
4. institutional repository
5. other authoritative stable source

Detailed procedure and gate: `references/GOALS-G04-G07.md`.

### G07 — Validate every URL

Check both reachability and work identity. A URL is not valid merely because it returns a page; the destination must correspond to the cited work. Repair canonical URLs when strong evidence supports the replacement and report unresolved cases.

Detailed procedure and gate: `references/GOALS-G04-G07.md`.

### G08 — Final integrity and citation audit

Perform a complete source/citation audit, recheck alphabetization, compare the non-citation fingerprint against preflight, inspect DOCX structural changes, update/verify field results without forcing update-on-open, perform Word field/open-safety checks, render the final DOCX, and visually inspect every page.

The pass succeeds only when any differences outside citations/references are explained by necessary field/bookmark/list machinery and no substantive non-citation content changed.

Detailed procedure and gate: `references/GOALS-G08-G10.md`.

### G09 — Produce final outputs

Produce:
- `<original-stem>-references-cleaned.docx`
- `BibTex.md`
- `references.bib`
- `Reference-Audit.md`
- `Suggested-References.md`

`BibTex.md` must contain the complete BibTeX collection in a fenced `bibtex` block. `references.bib` must contain the same BibTeX entries as a conventional `.bib` file.

Detailed output requirements: `references/GOALS-G08-G10.md`.

### G10 — Recommend 3-4 additional literature references

Search the literature in the context of the paper and identify 3-4 strong candidate works the author may wish to add. **Do not add them to the Word document or main bibliography.** For each candidate include verified metadata, DOI/canonical URL, relevance, and the specific existing sentence/paragraph/section where it might support the manuscript.

Write the recommendations to `Suggested-References.md` and summarize them to the user.

Detailed procedure: `references/GOALS-G08-G10.md`.

## Citation behavior examples

- Existing numeric: `[12]` -> `[<live REF field for the same source>]`.
- Group: `[4, 7]` -> `[4], [7]`, with each number its own field.
- Range: `[4-6]` or `[4–6]` -> `[4], [5], [6]` only when original source mapping is safe.
- Parenthetical author-year: `(Smith and Jones, 2024)` -> `[N]` when uniquely resolved.
- Narrative author-year: `Smith and Jones (2024)` -> `Smith and Jones [N]`; author prose stays unchanged.
- Ambiguous author-year: do not guess; preserve conservatively and report it.

See `references/PREFLIGHT-AND-CITATIONS.md` for complete rules.

## Completion rule

Before delivery, run the final acceptance checklist in `references/FAILURE-AND-ACCEPTANCE.md`. The user-facing summary must state what was actually validated, what remains unresolved, whether the non-citation integrity check passed, and provide all requested output files.
