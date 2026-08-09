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
  if (operation !== "draft" && operation !== "send") throw new Error("Unsupported Apple Mail write operation");
  const mail = Application("Mail");
  return JSON.stringify(compose(mail, operation, payload));
}
