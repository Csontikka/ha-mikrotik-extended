# Release testing

Every release of this integration goes through this list. It is not advice, it is
the gate: a release does not ship until every applicable box is ticked, and a box
is only ticked when an artifact proves it, never because a step "should" have
worked.

The list exists because a green test suite is not enough. Version 0.7.0 shipped a
defect where two core device sensors reported confidently wrong numbers: the unit
suite was green, the linters were clean, and a live test on two routers still
missed it, because every check asked "was this method called" instead of "is this
value right". Sections 3 and 4 are the direct answer to that.

## 0. Scope check

Before anything else, classify the change. The rest of the list is filtered by this.

- [ ] Does it touch **when data is fetched** (any condition in front of a `get_*`
      call in `_async_update_data`)? If yes, section 4 is mandatory in full.
- [ ] Does it touch **which entities are created** (`_skip_sensor` and friends,
      `enable_on_option`, entity descriptions)? If yes, sections 3 and 4 apply.
- [ ] Does it touch the **config or options flow**? If yes, section 5 applies.
- [ ] Does it change **anything a user sees**? If yes, section 7 applies.

## 1. Local gates

- [ ] `ruff check custom_components tests`
- [ ] `ruff format --check custom_components tests`
- [ ] `pytest tests/ -q` green, and the test count went **up** if behavior changed.
      A behavior change with an unchanged test count means the change is untested.
- [ ] New tests assert **values**, not just that a function was called. A test that
      only asserts `mock.assert_called_once()` cannot catch a wrong number.
- [ ] `python -m json.tool` on `manifest.json`, `hacs.json` and every file under
      `custom_components/mikrotik_extended/translations/`.

## 2. Data flow audit

Required whenever section 0 flagged a fetch-condition change. This is a manual
reading pass, and it is the step that would have caught the 0.7.0 defect.

- [ ] List every store key the change makes conditional.
- [ ] For each key, list **every** reader across the whole package, not just the
      obvious one. Include:
  - readers inside functions that run **unconditionally** in the update cycle,
    notably `async_process_host` and `process_interface_client`;
  - entity descriptions with `data_reference=""`. These are singletons routed
    through `_process_singleton`, where the skip filters are **never** called, so
    no option can suppress them. They exist in every preset and will happily
    display a value computed from missing data.
- [ ] For each reader, state what it produces when the store is empty. If the
      answer is a plausible looking value rather than unavailable, the change is
      wrong as designed. Fetch less data, not less correctness.
- [ ] Write the conclusion into the code as a comment next to the condition. The
      next person needs to know which consumers were considered.

## 3. Sensor correctness

- [ ] Every sensor the change can reach has its **value** checked against the
      router, not merely its existence.
- [ ] Core device sensors are checked in particular. They have no option of their
      own, so they survive every preset and are the most likely place for a silent
      error to reach a user.
- [ ] Client count sensors are compared against the router's actual client count.
- [ ] No entity is `unavailable`, and no entity is `unknown` that was not already
      `unknown` before the change.

## 4. Option matrix

The integration ships four presets plus arbitrary custom combinations. The
following are verified on a live router.

- [ ] **Core only** preset. This is the combination that shipped the 0.7.0 defect
      and it is the easiest one to forget, because it removes almost everything and
      therefore looks like it cannot break anything.
- [ ] **Minimal**, **Recommended** and **Full** presets.
- [ ] Interface entities off with host tracking **on**.
- [ ] Interface entities off with host tracking **off**.
- [ ] Interface entities on with host tracking **off**.
- [ ] **Invariance check:** across all of the above, the core device sensor set and
      their values must be identical, apart from values that genuinely drift such as
      CPU load, memory usage and uptime. Any other difference is a finding and must
      be explained before release.
- [ ] Switching a category off and back on restores the entities, and doing it a
      second time produces an identical result. A difference on the second cycle
      means something accumulates.
- [ ] For every new option: does it gate entity creation only, or also a fetch? If
      it gates a fetch, it gets its own row in this matrix.

## 5. Flows

- [ ] The setup wizard renders every step, and any new field or choice appears with
      the right label.
- [ ] The options flow applies and persists changes, and the entry reloads to
      `loaded`.
- [ ] Setting up a router that is already configured still aborts as a duplicate.
- [ ] Config entry options are written **only** through the flow APIs. The Home
      Assistant storage files are never edited directly, not even for a test fixture.

## 6. Live deployment

Nothing ships without this, regardless of how small the change looks.

- [ ] Deployed to the development Home Assistant instance and the entry reloaded. A
      change that migrates the entity or device registry needs a full restart, not a
      reload.
- [ ] Post deploy check script passes with zero failures.
- [ ] Tested against **two** routers with different topologies, and verified that a
      change applied to one config entry leaves the other entry untouched: same
      entity count, same devices, same values.
- [ ] Tested across a full Home Assistant restart, not only an entry reload. Cold
      start exercises the setup path, reload does not.
- [ ] Home Assistant's own error log contains no entry from this integration, in any
      of the states tested. Checked through the system log, since the error log
      endpoint is not always available.
- [ ] Side effects on adjacent functionality checked, not assumed. A change scoped
      to one area has broken an unrelated one before.
- [ ] Every configuration touched during testing is restored to its original value,
      and the restoration is verified by comparison, not by memory.

## 7. Release artifacts

- [ ] `manifest.json` version bumped, as its own commit, with a one line diff.
- [ ] The version matches the tag exactly. This is enforced in CI on tag push, but
      check it before tagging so a bad tag is never pushed.
- [ ] All CI checks green **on the release commit**, not merely on an earlier one.
- [ ] Release notes written by hand. Auto generated changelogs are never used.
- [ ] For a fix to a silently wrong value, the notes say plainly who was affected
      and what to re-check after updating. This class of defect produces no error
      message, so the release notes are the only signal the user gets.
- [ ] After publishing, the attached archive is **downloaded and inspected**: it
      contains the expected version, the change itself is present, and anything the
      change removed is absent.
- [ ] If a released version turns out to be defective, it is marked as a
      pre-release with a note pointing to the replacement, so it stops being offered
      as the current version.

## 8. Public content

Applies to commit messages, pull requests, issues, releases and documentation.

- [ ] No real host names, device names, network names or addresses. Placeholders
      only.
- [ ] No credentials, tokens or keys, and no file that contains them.
- [ ] No em dashes and no emoji.
- [ ] No AI authorship trailers or generated-by notices.
- [ ] Reporters and contributors are credited by name in the release notes and
      thanked on the issue or pull request.

## 9. Follow up

- [ ] Anything noticed but not fixed is written down as an issue, not left in a
      conversation.
- [ ] Any unexplained number seen during testing is chased down before release. An
      unexplained difference in a measurement is a finding, not noise. The 0.7.0
      defect was visible as a client count that moved by three during testing and
      was not investigated.
