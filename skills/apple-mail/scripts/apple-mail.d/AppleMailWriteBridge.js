#!/usr/bin/osascript -l JavaScript
/* Privileged internal Mail.app mutation bridge. Invoke only through apple-mail-write.py. */

function addRecipients(mail, message, values, kind) {
  const constructor = kind === "to" ? mail.ToRecipient : kind === "cc" ? mail.CcRecipient : mail.BccRecipient;
  const collection = kind === "to" ? message.toRecipients : kind === "cc" ? message.ccRecipients : message.bccRecipients;
  values.forEach((address) => collection.push(constructor({address: address})));
}

/* Attachments are inline rich-text elements. Pushing onto content.attachments inserts the file at
   the start of the body, so target the final paragraph explicitly. Mail silently ignores a push
   onto the message's own attachments collection after the first file. */
function addAttachments(mail, message, values) {
  values.forEach((candidate) => {
    const file = stringValue(candidate);
    if (file.charAt(0) !== "/") throw new Error("Attachment paths must be absolute local file paths");
    const paragraphs = message.content.paragraphs;
    if (!paragraphs.length) throw new Error("Mail.app did not expose message content for attachment insertion");
    paragraphs[paragraphs.length - 1].attachments.push(mail.Attachment({fileName: file}));
  });
}

function nativeBodyText(value) {
  return normalizedLines(value);
}

function nativeComposeItems(payload) {
  const items = [$(nativeBodyText(payload.body))];
  (payload.attachments || []).forEach((candidate) => {
    const file = stringValue(candidate);
    if (file.charAt(0) !== "/") throw new Error("Attachment paths must be absolute local file paths");
    items.push($.NSURL.fileURLWithPath($(file)));
  });
  return $(items);
}

function openNativeComposer(payload) {
  ObjC.import("AppKit");
  const service = $.NSSharingService.sharingServiceNamed($.NSSharingServiceNameComposeEmail);
  if (!service) throw new Error("macOS did not provide the native Mail compose service");
  service.subject = $(stringValue(payload.subject));
  service.recipients = $((payload.to || []).map(stringValue));
  service.performWithItems(nativeComposeItems(payload));
}

function matchingNativeWindows(systemEvents, subject) {
  const mailProcess = systemEvents.processes.byName("Mail");
  return {
    process: mailProcess,
    windows: mailProcess.windows().filter((window) => {
      return stringValue(callValue(window, "name", "")) === stringValue(subject);
    }),
  };
}

function savedDraftsWithSubject(mail, subject) {
  const drafts = mail.draftsMailbox();
  return drafts.messages().filter((message) => {
    return stringValue(callValue(message, "subject", "")) === stringValue(subject);
  });
}

function preflightNativeDraft(mail, payload, systemEvents) {
  const matches = matchingNativeWindows(systemEvents || Application("System Events"), payload.subject);
  if (matches.windows.length) {
    throw new Error("Close the existing Mail composer with this exact subject before retrying");
  }
  if (savedDraftsWithSubject(mail, payload.subject).length) {
    throw new Error("A saved Mail draft already uses this exact subject");
  }
}

function waitForNativeWindow(systemEvents, subject, pause) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const matches = matchingNativeWindows(systemEvents, subject);
    if (matches.windows.length === 1) return {process: matches.process, window: matches.windows[0]};
    if (matches.windows.length > 1) throw new Error("Mail opened more than one matching native composer");
    pause(0.1);
  }
  throw new Error("Accessibility did not expose the native composer before the timeout");
}

function composerAttachmentDescriptions(window) {
  const webAreas = (callValue(window, "entireContents", []) || []).filter((element) => {
    return stringValue(callValue(element, "role", "")) === "AXWebArea";
  });
  if (webAreas.length !== 1) return [];
  return (callValue(webAreas[0], "entireContents", []) || []).filter((element) => {
    return stringValue(callValue(element, "role", "")) === "AXButton";
  }).map((element) => stringValue(callValue(element, "description", ""))).filter(Boolean);
}

function waitForNativeAttachments(window, payload, pause) {
  const expected = (payload.attachment_metadata || (payload.attachments || []).map((path) => ({
    name: stringValue(path).split("/").pop(),
  }))).map((item) => stringValue(item.name));
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const descriptions = composerAttachmentDescriptions(window);
    const matched = descriptions.length === expected.length && expected.every((name, index) => {
      return descriptions[index] === name || descriptions[index].indexOf(name + ",") === 0;
    });
    if (matched) return;
    pause(0.1);
  }
  throw new Error("Mail's native composer did not finish loading the approved attachments before the timeout");
}

function finishNativeDraftWithAccessibility(payload, pause, injectedSystemEvents) {
  const systemEvents = injectedSystemEvents || Application("System Events");
  const target = waitForNativeWindow(systemEvents, payload.subject, pause);
  target.process.frontmost = true;
  const senderControls = target.window.popUpButtons();
  if (!senderControls.length) throw new Error("Accessibility did not expose Mail's From control");
  const selectedSender = stringValue(callValue(senderControls[0], "value", "")).toLowerCase();
  const senderParts = selectedSender.split(/[<>\s–—()]+/).filter(Boolean);
  if (senderParts.indexOf(stringValue(payload.from).toLowerCase()) === -1) {
    throw new Error("Mail's native composer selected a different From address");
  }
  waitForNativeAttachments(target.window, payload, pause);
  const raiseAction = target.window.actions.byName("AXRaise");
  if (!raiseAction.exists()) throw new Error("Accessibility did not expose Mail's window raise action");
  raiseAction.perform();
  systemEvents.keystroke("s", {using: "command down"});
  pause(0.5);
  raiseAction.perform();
  systemEvents.keystroke("w", {using: "command down"});
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const stillOpen = target.process.windows().some((window) => {
      return stringValue(callValue(window, "name", "")) === stringValue(payload.subject);
    });
    if (!stillOpen) return;
    pause(0.1);
  }
  throw new Error("Mail did not close the saved native composer before the timeout");
}

function normalizedLines(value) {
  return stringValue(value).replace(/\r\n?|\u2028|\u2029/g, "\n");
}

function canonicalPayloadBody(value) {
  return normalizedLines(value).replace(/\uFFFC/g, "");
}

function canonicalMailBody(value) {
  let body = canonicalPayloadBody(value);
  if (body.endsWith("\n\n")) body = body.slice(0, -2);
  return body;
}

function draftMatchesPayload(message, payload) {
  const sender = stringValue(callValue(message, "sender", "")).toLowerCase();
  const senderParts = sender.split(/[<>\s–—()]+/).filter(Boolean);
  if (senderParts.indexOf(stringValue(payload.from).toLowerCase()) === -1) return false;
  const to = (callValue(message, "toRecipients", []) || []).map((recipient) => {
    return stringValue(callValue(recipient, "address", "")).toLowerCase();
  });
  const expectedTo = (payload.to || []).map((address) => stringValue(address).toLowerCase());
  if (JSON.stringify(to) !== JSON.stringify(expectedTo)) return false;
  if ((callValue(message, "ccRecipients", []) || []).length) return false;
  if ((callValue(message, "bccRecipients", []) || []).length) return false;
  const content = canonicalMailBody(callValue(message, "content", ""));
  const body = canonicalPayloadBody(payload.body);
  if (content !== body) return false;
  const names = (callValue(message, "mailAttachments", []) || []).map((attachment) => {
    return stringValue(callValue(attachment, "name", ""));
  });
  const sizes = (callValue(message, "mailAttachments", []) || []).map((attachment) => {
    return Number(callValue(attachment, "fileSize", -1));
  });
  const expected = payload.attachment_metadata || (payload.attachments || []).map((path) => ({
    name: stringValue(path).split("/").pop(), bytes: -1,
  }));
  if (JSON.stringify(names) !== JSON.stringify(expected.map((item) => stringValue(item.name)))) return false;
  return expected.every((item, index) => Number(item.bytes) < 0 || Number(item.bytes) === sizes[index]);
}

function verifySavedNativeDraft(mail, payload, pause) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const candidates = savedDraftsWithSubject(mail, payload.subject);
    if (candidates.length > 1) throw new Error("More than one saved draft has the approved subject");
    if (candidates.length === 1 && draftMatchesPayload(candidates[0], payload)) {
      const source = stringValue(callValue(candidates[0], "source", ""));
      if (!source) {
        pause(0.1);
        continue;
      }
      return {
        id: stringValue(callValue(candidates[0], "id", "")),
        source: source,
      };
    }
    pause(0.1);
  }
  throw new Error("Mail did not persist an exact body, recipient, sender, and attachment match before the timeout");
}

function mimeDraftMatchesPayload(message, payload) {
  const sender = stringValue(callValue(message, "sender", "")).toLowerCase();
  const senderParts = sender.split(/[<>\s–—()]+/).filter(Boolean);
  if (senderParts.indexOf(stringValue(payload.from).toLowerCase()) === -1) return false;
  const to = (callValue(message, "toRecipients", []) || []).map((recipient) => {
    return stringValue(callValue(recipient, "address", "")).toLowerCase();
  });
  const expectedTo = (payload.to || []).map((address) => stringValue(address).toLowerCase());
  if (JSON.stringify(to) !== JSON.stringify(expectedTo)) return false;
  if ((callValue(message, "ccRecipients", []) || []).length) return false;
  if ((callValue(message, "bccRecipients", []) || []).length) return false;
  const attachments = callValue(message, "mailAttachments", []) || [];
  const names = attachments.map((attachment) => stringValue(callValue(attachment, "name", "")));
  const sizes = attachments.map((attachment) => Number(callValue(attachment, "fileSize", -1)));
  const expected = payload.attachment_metadata || [];
  if (JSON.stringify(names) !== JSON.stringify(expected.map((item) => stringValue(item.name)))) return false;
  return expected.every((item, index) => Number(item.bytes) === sizes[index]);
}

function verifySavedMimeDraft(mail, payload, pause) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const candidates = savedDraftsWithSubject(mail, payload.subject);
    if (candidates.length > 1) throw new Error("More than one saved draft has the approved subject");
    if (candidates.length === 1 && mimeDraftMatchesPayload(candidates[0], payload)) {
      const source = stringValue(callValue(candidates[0], "source", ""));
      if (source) {
        return {id: stringValue(callValue(candidates[0], "id", "")), source: source};
      }
    }
    pause(0.1);
  }
  throw new Error("Mail did not persist the imported draft envelope and attachments before the timeout");
}

function openMimeDraft(mail, payload) {
  const path = stringValue(payload.eml_path);
  if (path.charAt(0) !== "/") throw new Error("The approved MIME draft path must be absolute");
  const opened = Boolean($.NSWorkspace.sharedWorkspace.openURL($.NSURL.fileURLWithPath($(path))));
  if (!opened) throw new Error("macOS did not open the approved MIME draft in Mail");
}

function moveOpenMimeDraft(payload, pause, injectedSystemEvents) {
  const systemEvents = injectedSystemEvents || Application("System Events");
  const target = waitForNativeWindow(systemEvents, payload.subject, pause);
  target.process.frontmost = true;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const moveButtons = target.window.entireContents().filter((element) => {
      return stringValue(callValue(element, "role", "")) === "AXMenuButton"
        && stringValue(callValue(element, "description", "")) === "Move";
    });
    if (moveButtons.length === 1) {
      moveButtons[0].click();
      pause(0.1);
      if (moveButtons[0].menus().length === 1) {
        const allDrafts = moveButtons[0].menus[0].menuItems.byName("All Drafts");
        if (allDrafts.exists() && allDrafts.menus().length === 1) {
          const destinations = allDrafts.menus[0].menuItems().filter((item) => {
            return stringValue(callValue(item, "name", "")) === stringValue(payload.account_name);
          });
          if (destinations.length === 1 && Boolean(callValue(destinations[0], "enabled", false))) {
            destinations[0].click();
            pause(0.5);
            return {systemEvents: systemEvents, process: target.process, window: target.window};
          }
        }
      }
      systemEvents.keyCode(53);
    }
    pause(0.1);
  }
  throw new Error("Mail did not expose one exact approved Drafts account destination before the timeout");
}

function closeMimeDraftWindow(target, payload, pause) {
  const raiseAction = target.window.actions.byName("AXRaise");
  if (!raiseAction.exists()) throw new Error("Accessibility did not expose Mail's window raise action");
  raiseAction.perform();
  target.systemEvents.keystroke("w", {using: "command down"});
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const stillOpen = target.process.windows().some((window) => {
      return stringValue(callValue(window, "name", "")) === stringValue(payload.subject);
    });
    if (!stillOpen) return;
    pause(0.1);
  }
  throw new Error("Mail did not close the imported draft viewer before the timeout");
}

function mimeDraft(mail, payload, preflight, openDraft, moveDraft, verifyDraft, closeDraft) {
  validateAccountSelection(mail.accounts(), payload);
  let opened = false;
  let stage = "checking Mail before MIME import";
  try {
    preflight(mail, payload);
    stage = "opening the standard MIME draft";
    openDraft(mail, payload);
    opened = true;
    stage = "moving the standard MIME message into Drafts";
    const target = moveDraft(payload);
    stage = "verifying the saved standard MIME draft";
    const verified = verifyDraft(mail, payload);
    stage = "closing the imported draft viewer";
    closeDraft(target, payload);
    return {
      status: "ok",
      operation: "draft",
      attachments: (payload.attachments || []).length,
      saved_draft_id: verified.id,
      saved_draft_source: verified.source,
    };
  } catch (error) {
    const recovery = opened
      ? "; an imported message or partial draft may remain, so verify Mail before retrying"
      : "";
    throw new Error(stage + " failed: " + error.message + recovery);
  }
}

function nativeDraft(mail, payload, preflight, openComposer, finishComposer, verifyDraft) {
  validateAccountSelection(mail.accounts(), payload);
  let opened = false;
  let stage = "checking Accessibility before native composition";
  try {
    preflight(mail, payload);
    stage = "opening the native composer";
    openComposer(payload);
    opened = true;
    stage = "saving the native draft through Accessibility";
    finishComposer(payload);
    stage = "verifying the saved native draft";
    const verified = verifyDraft(mail, payload);
    const result = {status: "ok", operation: "draft", attachments: (payload.attachments || []).length};
    if (verified && typeof verified === "object") {
      result.saved_draft_id = verified.id;
      result.saved_draft_source = verified.source;
    }
    return result;
  } catch (error) {
    const recovery = opened
      ? "; a partial native composer may remain open or in Drafts, so verify Mail before retrying"
      : "";
    throw new Error(stage + " failed: " + error.message + recovery);
  }
}

function stringValue(value) {
  try { return String(value === null || value === undefined ? "" : value); } catch (_) { return ""; }
}

function callValue(object, property, fallback) {
  try {
    const value = object[property]();
    return value === null || value === undefined ? fallback : value;
  } catch (_) {
    return fallback;
  }
}

function validateAccountSelection(accounts, payload) {
  const sender = stringValue(payload.from).toLowerCase();
  const matches = accounts.filter((account) => {
    const addresses = callValue(account, "emailAddresses", []) || [];
    return addresses.some((address) => stringValue(address).toLowerCase() === sender);
  });
  if (matches.length !== 1) throw new Error("Sender address must map to exactly one configured Mail account");
  const matchedId = stringValue(callValue(matches[0], "id", ""));
  if (matchedId !== stringValue(payload.account_id)) throw new Error("Sender address does not map to the approved Mail account");
}

function compose(mail, operation, payload) {
  validateAccountSelection(mail.accounts(), payload);
  const message = mail.OutgoingMessage({
    sender: payload.from,
    subject: payload.subject,
    visible: false,
  });
  const attachments = payload.attachments || [];
  let inserted = false;
  try {
    /* Mail exposes an outgoing message's recipient and attachment collections only after it is
       inserted. Set plain text only after insertion so Mail's composer supplies its native text
       attributes instead of preserving a pre-composer rich-text run that renders badly in dark
       mode. A trailing empty paragraph keeps inline attachments after the body. */
    mail.outgoingMessages.push(message);
    inserted = true;
    message.content = payload.body + (attachments.length ? "\n\n" : "");
    addRecipients(mail, message, payload.to || [], "to");
    addRecipients(mail, message, payload.cc || [], "cc");
    addRecipients(mail, message, payload.bcc || [], "bcc");
    addAttachments(mail, message, attachments);
    if (operation === "draft") {
      message.save();
      return {status: "ok", operation: "draft", attachments: attachments.length};
    }
    const sent = Boolean(message.send());
    if (!sent) throw new Error("Mail.app did not confirm the send request");
    return {status: "ok", operation: "send", attachments: attachments.length};
  } catch (error) {
    if (inserted) {
      try {
        mail.delete(message);
      } catch (cleanupError) {
        throw new Error(error.message + "; Mail could not remove the partial outgoing message: " + cleanupError.message);
      }
    }
    throw error;
  }
}

function fakeMail(payload, scenario, events) {
  let composedContent = "";
  function account(record) {
    return {id: function () { return record.id; }, emailAddresses: function () { return record.email_addresses; }};
  }
  function collection(name) {
    const items = [];
    return {
      items: items,
      push: function (item) {
        events.push(name);
        if (scenario === name + "-fails") throw new Error("synthetic " + name + " failure");
        items.push(item);
      },
    };
  }
  return {
    accounts: function () { return (payload.synthetic_accounts || []).map(account); },
    ToRecipient: function (record) { return record; },
    CcRecipient: function (record) { return record; },
    BccRecipient: function (record) { return record; },
    Attachment: function (record) { return record; },
    OutgoingMessage: function (record) {
      let rootAttachments = null;
      let paragraphAttachments = null;
      let content = stringValue(record.content);
      if (Object.prototype.hasOwnProperty.call(record, "content")) events.push("constructor-content");
      Object.defineProperty(record, "content", {
        configurable: true,
        get: function () {
          return {
            attachments: rootAttachments,
            paragraphs: scenario === "no-paragraphs" ? [] : [{attachments: paragraphAttachments}],
          };
        },
        set: function (value) {
          content = stringValue(value);
          composedContent = content;
          events.push(content.endsWith("\n\n") ? "content:separated" : "content:plain");
        },
      });
      record.save = function () { events.push("save"); if (scenario === "save-fails") throw new Error("synthetic save failure"); };
      record.send = function () { events.push("send"); return scenario !== "send-fails"; };
      record.insert = function () {
        rootAttachments = collection("root-attach");
        paragraphAttachments = collection("paragraph-attach");
      };
      return record;
    },
    /* Mirrors Mail: recipient and content attachment collections exist only once the message is
       inserted, and attachments hang off the content element rather than the message. */
    outgoingMessages: {push: function (record) {
      events.push("push");
      record.toRecipients = collection("recipient");
      record.ccRecipients = collection("recipient");
      record.bccRecipients = collection("recipient");
      record.insert();
    }},
    delete: function () { events.push("delete"); },
    testContent: function () { return composedContent; },
  };
}

function fakeNativeHarness(payload, scenario, events) {
  return {
    mail: {
      accounts: function () {
        return (payload.synthetic_accounts || []).map((record) => ({
          id: function () { return record.id; },
          emailAddresses: function () { return record.email_addresses; },
        }));
      },
    },
    preflight: function () {
      events.push("accessibility-preflight");
      if (scenario === "accessibility-denied") throw new Error("synthetic Accessibility denial");
      if (scenario === "duplicate-subject") throw new Error("synthetic duplicate subject");
    },
    open: function () {
      events.push("native-open");
      if (scenario === "open-fails") throw new Error("synthetic native open failure");
    },
    finish: function () {
      events.push("accessibility-finish");
      if (scenario === "sender-mismatch") throw new Error("synthetic From mismatch");
      if (scenario === "save-timeout") throw new Error("synthetic save timeout");
    },
    verify: function () {
      events.push("verify-saved-draft");
      if (scenario === "persistence-mismatch") throw new Error("synthetic persistence mismatch");
    },
  };
}

function fakeSavedMessage(record) {
  function recipients(values) {
    return (values || []).map((address) => ({address: function () { return address; }}));
  }
  return {
    sender: function () { return record.sender; },
    subject: function () { return record.subject; },
    toRecipients: function () { return recipients(record.to); },
    ccRecipients: function () { return recipients(record.cc); },
    bccRecipients: function () { return recipients(record.bcc); },
    content: function () { return record.content; },
    mailAttachments: function () {
      return (record.attachments || []).map((item) => ({
        name: function () { return typeof item === "string" ? item : item.name; },
        fileSize: function () { return typeof item === "string" ? -1 : item.bytes; },
      }));
    },
    id: function () { return record.id || "synthetic-id"; },
    source: function () { return record.source || "synthetic-source"; },
  };
}

function fakeNativeSafety(payload, scenario, events) {
  let closed = false;
  let attachmentReads = 0;
  const verifierScenarios = [
    "saved-subject", "body-mismatch", "trailing-space-mismatch", "extra-blank-line", "exact-saved",
    "sender-substring", "to-mismatch", "cc-mismatch", "bcc-mismatch",
    "missing-attachment", "wrong-filename", "wrong-size", "wrong-order",
  ];
  const otherWindow = {
    name: function () { return "Unrelated composer"; },
    popUpButtons: function () { return [{value: function () { return "wrong@example.test"; }}]; },
    actions: {byName: function () {
      return {exists: function () { return true; }, perform: function () { events.push("raise:other"); }};
    }},
  };
  const targetWindow = {
    name: function () { return payload.subject; },
    popUpButtons: function () {
      const sender = scenario === "sender-mismatch" ? "wrong@example.test" : payload.from;
      return [{value: function () { return sender; }}];
    },
    entireContents: function () {
      return [{
        role: function () { return "AXWebArea"; },
        entireContents: function () {
          attachmentReads += 1;
          if (scenario === "composer-missing-attachment") return [];
          if (scenario === "composer-delayed-attachment" && attachmentReads < 3) return [];
          return (payload.attachment_metadata || []).map((item) => ({
            role: function () { return "AXButton"; },
            description: function () { return item.name + ", " + item.bytes + " bytes"; },
          }));
        },
      }];
    },
    actions: {byName: function (name) {
      events.push("action:" + name);
      return {exists: function () { return true; }, perform: function () { events.push("raise:target"); }};
    }},
  };
  const process = {
    windows: function () {
      const windows = [otherWindow];
      const verifierOnly = verifierScenarios.indexOf(scenario) !== -1;
      if (!closed && !verifierOnly) windows.push(targetWindow);
      if (scenario === "duplicate-window") windows.push(targetWindow);
      return windows;
    },
    set frontmost(value) { events.push("frontmost:" + String(value)); },
  };
  const systemEvents = {
    processes: {byName: function (name) { events.push("process:" + name); return process; }},
    keystroke: function (key) {
      events.push("key:" + key);
      if (key === "w") closed = true;
    },
  };
  const exactRecord = {
    sender: "Sender <" + payload.from + ">",
    subject: payload.subject,
    to: payload.to || [],
    cc: [],
    bcc: [],
    content: nativeBodyText(payload.body) + "\n\n",
    attachments: (payload.attachment_metadata || (payload.attachments || []).map((path) => ({
      name: stringValue(path).split("/").pop(), bytes: -1,
    }))).map((item) => ({
      name: item.name, bytes: item.bytes,
    })),
  };
  if (scenario === "body-mismatch") exactRecord.content = exactRecord.content.replace("Second", "Changed");
  if (scenario === "trailing-space-mismatch") exactRecord.content = exactRecord.content.replace("  \n\n", " \n\n");
  if (scenario === "extra-blank-line") exactRecord.content = exactRecord.content.replace("\nSecond", "\n\nSecond");
  if (scenario === "sender-substring") exactRecord.sender = "Not Sender <not" + payload.from + ">";
  if (scenario === "to-mismatch") exactRecord.to = ["wrong@example.test"];
  if (scenario === "cc-mismatch") exactRecord.cc = ["wrong@example.test"];
  if (scenario === "bcc-mismatch") exactRecord.bcc = ["wrong@example.test"];
  if (scenario === "missing-attachment") exactRecord.attachments = [];
  if (scenario === "wrong-filename" && exactRecord.attachments.length) {
    exactRecord.attachments[0].name = "wrong-" + exactRecord.attachments[0].name;
  }
  if (scenario === "wrong-size" && exactRecord.attachments.length) exactRecord.attachments[0].bytes += 1;
  if (scenario === "wrong-order") exactRecord.attachments.reverse();
  const savedRecords = verifierScenarios.indexOf(scenario) !== -1 ? [exactRecord] : [];
  const mail = {
    draftsMailbox: function () {
      return {messages: function () { return savedRecords.map(fakeSavedMessage); }};
    },
  };
  return {mail: mail, systemEvents: systemEvents};
}

function run(argv) {
  const operation = argv[0] || "";
  const payload = JSON.parse(argv[1] || "{}");
  if (operation === "_test_compose") {
    const events = [];
    const requestedOperation = payload.test_operation || "draft";
    const mail = fakeMail(payload, payload.test_scenario || "ok", events);
    try {
      const result = compose(mail, requestedOperation, payload);
      return JSON.stringify({result: result, events: events, content: mail.testContent()});
    } catch (error) {
      return JSON.stringify({error: error.message, events: events, content: mail.testContent()});
    }
  }
  if (operation === "_test_native_compose") {
    const events = [];
    const harness = fakeNativeHarness(payload, payload.test_scenario || "ok", events);
    try {
      const result = nativeDraft(
        harness.mail, payload, harness.preflight, harness.open, harness.finish, harness.verify
      );
      return JSON.stringify({result: result, events: events});
    } catch (error) {
      return JSON.stringify({error: error.message, events: events});
    }
  }
  if (operation === "_test_mime_compose") {
    const events = [];
    const scenario = payload.test_scenario || "ok";
    const mail = {
      accounts: function () {
        return (payload.synthetic_accounts || []).map((record) => ({
          id: function () { return record.id; },
          emailAddresses: function () { return record.email_addresses; },
        }));
      },
    };
    try {
      const result = mimeDraft(
        mail,
        payload,
        function () { events.push("preflight"); },
        function () { events.push("open"); if (scenario === "open-fails") throw new Error("open failure"); },
        function () { events.push("move"); if (scenario === "move-fails") throw new Error("move failure"); return {}; },
        function () { events.push("verify"); if (scenario === "verify-fails") throw new Error("verify failure"); return {id: "id", source: "source"}; },
        function () { events.push("close"); if (scenario === "close-fails") throw new Error("close failure"); }
      );
      return JSON.stringify({result: result, events: events});
    } catch (error) {
      return JSON.stringify({error: error.message, events: events});
    }
  }
  if (operation === "_test_native_body") {
    return JSON.stringify({
      body: nativeBodyText(payload.body),
      canonical_body: canonicalPayloadBody(payload.body),
      canonical_mail_body: canonicalMailBody(payload.test_saved_body || payload.body),
    });
  }
  if (operation === "_test_native_safety") {
    const events = [];
    const scenario = payload.test_scenario || "finish";
    const harness = fakeNativeSafety(payload, scenario, events);
    const pause = function () { events.push("pause"); };
    try {
      if (scenario === "open-subject" || scenario === "saved-subject") {
        if (scenario === "open-subject") payload.subject = "Unrelated composer";
        preflightNativeDraft(harness.mail, payload, harness.systemEvents);
      } else if ([
        "exact-saved", "body-mismatch", "trailing-space-mismatch", "extra-blank-line", "sender-substring",
        "to-mismatch", "cc-mismatch", "bcc-mismatch", "missing-attachment",
        "wrong-filename", "wrong-size", "wrong-order",
      ].indexOf(scenario) !== -1) {
        verifySavedNativeDraft(harness.mail, payload, pause);
      } else {
        finishNativeDraftWithAccessibility(payload, pause, harness.systemEvents);
      }
      return JSON.stringify({status: "ok", events: events});
    } catch (error) {
      return JSON.stringify({error: error.message, events: events});
    }
  }
  if (operation === "accessibility-status") {
    const systemEvents = Application("System Events");
    systemEvents.processes.byName("Mail").windows();
    return JSON.stringify({status: "ok", accessibility: true});
  }
  if (operation === "draft-mime") {
    ObjC.import("AppKit");
    const mail = Application("Mail");
    const pause = function (seconds) {
      $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(seconds));
    };
    return JSON.stringify(mimeDraft(
      mail,
      payload,
      preflightNativeDraft,
      openMimeDraft,
      function (draftPayload) { return moveOpenMimeDraft(draftPayload, pause); },
      function (draftMail, draftPayload) { return verifySavedMimeDraft(draftMail, draftPayload, pause); },
      function (target, draftPayload) { return closeMimeDraftWindow(target, draftPayload, pause); }
    ));
  }
  if (operation !== "draft" && operation !== "send") throw new Error("Unsupported Apple Mail write operation");
  const mail = Application("Mail");
  if ((payload.attachments || []).length) {
    if (operation !== "draft") throw new Error("Attachment-bearing sends must be completed from Mail's native draft");
    const pause = function (seconds) {
      $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(seconds));
    };
    return JSON.stringify(nativeDraft(
      mail,
      payload,
      preflightNativeDraft,
      openNativeComposer,
      function (draftPayload) { finishNativeDraftWithAccessibility(draftPayload, pause); },
      function (draftMail, draftPayload) {
        return verifySavedNativeDraft(draftMail, draftPayload, pause);
      }
    ));
  }
  return JSON.stringify(compose(mail, operation, payload));
}
