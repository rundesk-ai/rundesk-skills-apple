#!/usr/bin/osascript -l JavaScript
/* Privileged internal Mail.app mutation bridge. Invoke only through apple-mail-write.py. */

function addRecipients(mail, message, values, kind) {
  const constructor = kind === "to" ? mail.ToRecipient : kind === "cc" ? mail.CcRecipient : mail.BccRecipient;
  const collection = kind === "to" ? message.toRecipients : kind === "cc" ? message.ccRecipients : message.bccRecipients;
  values.forEach((address) => collection.push(constructor({address: address})));
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
    content: payload.body,
    visible: false,
  });
  let inserted = false;
  try {
    addRecipients(mail, message, payload.to || [], "to");
    addRecipients(mail, message, payload.cc || [], "cc");
    addRecipients(mail, message, payload.bcc || [], "bcc");
    mail.outgoingMessages.push(message);
    inserted = true;
    if (operation === "draft") {
      message.save();
      return {status: "ok", operation: "draft"};
    }
    const sent = Boolean(message.send());
    if (!sent) throw new Error("Mail.app did not confirm the send request");
    return {status: "ok", operation: "send"};
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
  function account(record) {
    return {id: function () { return record.id; }, emailAddresses: function () { return record.email_addresses; }};
  }
  return {
    accounts: function () { return (payload.synthetic_accounts || []).map(account); },
    ToRecipient: function (record) { return record; },
    CcRecipient: function (record) { return record; },
    BccRecipient: function (record) { return record; },
    OutgoingMessage: function (record) {
      record.toRecipients = [];
      record.ccRecipients = [];
      record.bccRecipients = [];
      record.save = function () { events.push("save"); if (scenario === "save-fails") throw new Error("synthetic save failure"); };
      record.send = function () { events.push("send"); return scenario !== "send-fails"; };
      return record;
    },
    outgoingMessages: {push: function () { events.push("push"); }},
    delete: function () { events.push("delete"); },
  };
}

function run(argv) {
  const operation = argv[0] || "";
  const payload = JSON.parse(argv[1] || "{}");
  if (operation === "_test_compose") {
    const events = [];
    const requestedOperation = payload.test_operation || "draft";
    try {
      const result = compose(fakeMail(payload, payload.test_scenario || "ok", events), requestedOperation, payload);
      return JSON.stringify({result: result, events: events});
    } catch (error) {
      return JSON.stringify({error: error.message, events: events});
    }
  }
  if (operation !== "draft" && operation !== "send") throw new Error("Unsupported Apple Mail write operation");
  const mail = Application("Mail");
  return JSON.stringify(compose(mail, operation, payload));
}
