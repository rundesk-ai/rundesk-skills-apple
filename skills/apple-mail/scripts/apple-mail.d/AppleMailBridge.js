#!/usr/bin/osascript -l JavaScript
/* Read-only JXA bridge for Mail.app. Every command returns JSON on stdout. */

function stringValue(value, fallback) {
  try {
    if (value === null || value === undefined) return fallback || "";
    return String(value);
  } catch (_) {
    return fallback || "";
  }
}

function boundedString(value, limit) {
  return Array.from(stringValue(value, "")).slice(0, limit).join("");
}

function callValue(object, property, fallback) {
  try {
    const value = object[property]();
    return value === null || value === undefined ? fallback : value;
  } catch (_) {
    return fallback;
  }
}

function requiredValue(object, property) {
  const value = object[property]();
  if (value === null || value === undefined) throw new Error("Mail returned no value for " + property);
  return value;
}

function isoDate(value) {
  try {
    const date = new Date(value);
    return isNaN(date.getTime()) ? "" : date.toISOString();
  } catch (_) {
    return "";
  }
}

function accountRecord(account) {
  return {
    id: stringValue(callValue(account, "id", "")),
    name: stringValue(callValue(account, "name", "")),
    email_addresses: (callValue(account, "emailAddresses", []) || []).map(String),
    account_type: stringValue(callValue(account, "accountType", "")),
    enabled: Boolean(callValue(account, "enabled", false)),
  };
}

function findAccount(mail, accountId) {
  const matches = mail.accounts().filter((account) => stringValue(callValue(account, "id", "")) === accountId);
  if (matches.length !== 1) throw new Error("No unique Mail account matched account id " + accountId);
  return matches[0];
}

function mailboxChildren(mailbox) {
  return callValue(mailbox, "mailboxes", []) || [];
}

function flattenMailboxes(mailboxes, prefix, output) {
  mailboxes.forEach((mailbox) => {
    const name = stringValue(callValue(mailbox, "name", ""));
    const encodedName = encodeURIComponent(name).replace(/-/g, "%2D");
    const path = prefix ? prefix + "/" + encodedName : encodedName;
    output.push({
      path: path,
      name: name,
      unread_count: Number(callValue(mailbox, "unreadCount", 0)) || 0,
    });
    flattenMailboxes(mailboxChildren(mailbox), path, output);
  });
}

function findMailbox(account, path) {
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  if (!parts.length) throw new Error("Mailbox path is required");
  let candidates = callValue(account, "mailboxes", []) || [];
  let current = null;
  parts.forEach((part) => {
    const matches = candidates.filter((mailbox) => stringValue(callValue(mailbox, "name", "")) === part);
    if (matches.length !== 1) throw new Error("No unique mailbox matched path " + path);
    current = matches[0];
    candidates = mailboxChildren(current);
  });
  return current;
}

function addressRecords(items, limit) {
  const values = items || [];
  return values.slice(0, limit === undefined ? values.length : limit).map((item) => ({
    name: boundedString(callValue(item, "name", ""), 120),
    address: boundedString(callValue(item, "address", ""), 320),
  }));
}

function attachmentRecords(items, limit) {
  const values = items || [];
  return values.slice(0, limit === undefined ? values.length : limit).map((attachment) => ({
    id: boundedString(callValue(attachment, "id", ""), 160),
    name: boundedString(callValue(attachment, "name", ""), 240),
    mime_type: boundedString(callValue(attachment, "mimeType", ""), 120),
    file_size: Number(callValue(attachment, "fileSize", 0)) || 0,
    downloaded: Boolean(callValue(attachment, "downloaded", false)),
  }));
}

function messageMetadata(message) {
  return {
    id: Number(callValue(message, "id", 0)) || 0,
    message_id: stringValue(callValue(message, "messageId", "")),
    subject: stringValue(callValue(message, "subject", "")),
    sender: stringValue(callValue(message, "sender", "")),
    date_received: isoDate(callValue(message, "dateReceived", null)),
    date_sent: isoDate(callValue(message, "dateSent", null)),
    read: Boolean(callValue(message, "readStatus", false)),
    flagged: Boolean(callValue(message, "flaggedStatus", false)),
    junk: Boolean(callValue(message, "junkMailStatus", false)),
    message_size: Number(callValue(message, "messageSize", 0)) || 0,
  };
}

function addMessageDetails(message, record, contentLimit) {
  record.reply_to = stringValue(requiredValue(message, "replyTo"));
  const toRecipients = requiredValue(message, "toRecipients");
  const ccRecipients = requiredValue(message, "ccRecipients");
  const attachments = requiredValue(message, "mailAttachments");
  record.to = addressRecords(toRecipients, 50);
  record.to_omitted = Math.max(0, toRecipients.length - record.to.length);
  record.cc = addressRecords(ccRecipients, 50);
  record.cc_omitted = Math.max(0, ccRecipients.length - record.cc.length);
  record.attachments = attachmentRecords(attachments, 20);
  record.attachments_omitted = Math.max(0, attachments.length - record.attachments.length);
  if (contentLimit) {
    const content = stringValue(requiredValue(message, "content"))
      .replace(/\uFFFC/g, "")
      .replace(/[\u2028\u2029]/g, "\n");
    const characters = Array.from(content);
    record.content = characters.slice(0, contentLimit).join("");
    record.content_truncated = characters.length > contentLimit;
  }
  return record;
}

function addMessagePreview(message, record, previewChars) {
  try {
    const toRecipients = requiredValue(message, "toRecipients");
    record.to = addressRecords(toRecipients, 10);
    record.to_omitted = Math.max(0, toRecipients.length - record.to.length);
  } catch (_) {
    record.to = [];
    record.recipients_unavailable = true;
  }
  try {
    const content = stringValue(requiredValue(message, "content")).replace(/\uFFFC/g, "");
    const characters = Array.from(content.replace(/\s+/g, " ").trim());
    record.preview = characters.slice(0, previewChars).join("");
    record.preview_truncated = characters.length > previewChars;
  } catch (_) {
    record.preview = "";
    record.preview_unavailable = true;
  }
  return record;
}

function boundedMessages(mailbox, scanLimit) {
  const messages = callValue(mailbox, "messages", []) || [];
  return messages.slice(0, Math.min(messages.length, scanLimit));
}

function messageMatches(record, query, unreadOnly, sinceMs, messageId, selectedMessageIds) {
  if (unreadOnly && record.read) return false;
  if (messageId && String(record.id) !== messageId && record.message_id !== messageId) return false;
  if (selectedMessageIds.length && selectedMessageIds.indexOf(String(record.id)) === -1) return false;
  if (sinceMs && (!record.date_received || new Date(record.date_received).getTime() < sinceMs)) return false;
  if (query) {
    const haystack = (record.subject + "\n" + record.sender).toLowerCase();
    if (haystack.indexOf(query.toLowerCase()) === -1) return false;
  }
  return true;
}

function fakeMessage(fixture, accesses) {
  function simple(value) { return function () { return value; }; }
  function fakeAddress(record) {
    return {name: simple(record.name || ""), address: simple(record.address || "")};
  }
  function fakeAttachment(record) {
    return {
      id: simple(record.id || ""),
      name: simple(record.name || ""),
      mimeType: simple(record.mime_type || ""),
      fileSize: simple(record.file_size || 0),
      downloaded: simple(Boolean(record.downloaded)),
    };
  }
  return {
    id: simple(fixture.id),
    messageId: simple(fixture.message_id),
    subject: simple(fixture.subject),
    sender: simple(fixture.sender),
    dateReceived: simple(fixture.date_received),
    dateSent: simple(fixture.date_sent),
    readStatus: simple(fixture.read),
    flaggedStatus: simple(fixture.flagged),
    junkMailStatus: simple(fixture.junk),
    messageSize: simple(fixture.message_size),
    replyTo: simple(fixture.reply_to),
    toRecipients: simple((fixture.to || []).map(fakeAddress)),
    ccRecipients: simple((fixture.cc || []).map(fakeAddress)),
    mailAttachments: simple((fixture.attachments || []).map(fakeAttachment)),
    content: function () { accesses.content += 1; return fixture.content || ""; },
  };
}

function fakeMailbox(record, accesses) {
  function simple(value) { return function () { return value; }; }
  return {
    name: simple(record.name),
    unreadCount: simple(record.unread_count || 0),
    mailboxes: function () { return (record.mailboxes || []).map((child) => fakeMailbox(child, accesses)); },
    messages: function () { return (record.messages || []).map((message) => fakeMessage(message, accesses)); },
  };
}

function fakeAccount(record, accesses) {
  return {
    id: function () { return record.id; },
    mailboxes: function () { return (record.mailboxes || []).map((mailbox) => fakeMailbox(mailbox, accesses)); },
  };
}

function messagesPayload(account, mailboxPath, scanLimit, resultLimit, unreadOnly, query, sinceMs, contentMode, messageId, contentLimit, selectedMessageIds) {
  const mailbox = findMailbox(account, mailboxPath);
  const output = [];
  const messages = boundedMessages(mailbox, scanLimit);
  for (let index = 0; index < messages.length && output.length < resultLimit; index += 1) {
    const record = messageMetadata(messages[index]);
    if (!messageMatches(record, query, unreadOnly, sinceMs, messageId, selectedMessageIds)) continue;
    if (contentMode === "preview") addMessagePreview(messages[index], record, contentLimit);
    if (contentMode === "full") addMessageDetails(messages[index], record, contentLimit);
    output.push(record);
  }
  return {mailbox: mailboxPath, scanned: messages.length, messages: output};
}

function execute(argv) {
  const command = argv[0] || "";
  if (command === "_test_record") {
    const fixture = JSON.parse(argv[1] || "{}");
    const accesses = {content: 0};
    const message = fakeMessage(fixture, accesses);
    const record = messageMetadata(message);
    const matched = messageMatches(record, argv[2] || "", argv[3] === "1", 0, argv[4] || "", []);
    if (matched && (argv[5] === "1" || argv[5] === "full")) addMessageDetails(message, record, Number(argv[6] || 20000));
    if (matched && argv[5] === "preview") addMessagePreview(message, record, Number(argv[6] || 160));
    return {matched: matched, record: record, accesses: accesses};
  }
  if (command === "_test_scope") {
    const fixture = JSON.parse(argv[1] || "{}");
    const accesses = {content: 0};
    const fakeMail = {accounts: function () {
      return (fixture.accounts || []).map((account) => fakeAccount(account, accesses));
    }};
    try {
      const account = findAccount(fakeMail, argv[2] || "");
      const payload = messagesPayload(account, argv[3] || "INBOX", 250, 25, false, "", 0, "full", argv[4] || "", 20000, []);
      return {payload: payload, accesses: accesses};
    } catch (error) {
      return {error: error.message, accesses: accesses};
    }
  }
  if (command === "_test_mailboxes") {
    const fixture = JSON.parse(argv[1] || "{}");
    const accesses = {content: 0};
    const account = fakeAccount(fixture, accesses);
    const output = [];
    flattenMailboxes(account.mailboxes(), "", output);
    return output;
  }
  const mail = Application("Mail");
  if (command === "accounts") return mail.accounts().map(accountRecord);

  const accountId = argv[1] || "";
  const account = findAccount(mail, accountId);
  if (command === "mailboxes") {
    const output = [];
    flattenMailboxes(callValue(account, "mailboxes", []) || [], "", output);
    return output;
  }
  if (command !== "messages") throw new Error("Unsupported bridge command: " + command);

  const mailboxPath = argv[2] || "INBOX";
  const scanLimit = Math.max(1, Math.min(Number(argv[3] || 250), 2000));
  const resultLimit = Math.max(1, Math.min(Number(argv[4] || 25), 500));
  const unreadOnly = argv[5] === "1";
  const query = argv[6] || "";
  const sinceMs = argv[7] ? new Date(argv[7]).getTime() : 0;
  const contentMode = argv[8] || "none";
  const messageId = argv[9] || "";
  const contentLimit = Math.max(0, Math.min(Number(argv[10] || 0), 20000));
  const selectedMessageIds = argv[11] ? JSON.parse(argv[11]).map(String) : [];
  return messagesPayload(
    account,
    mailboxPath,
    scanLimit,
    resultLimit,
    unreadOnly,
    query,
    sinceMs,
    contentMode,
    messageId,
    contentLimit,
    selectedMessageIds
  );
}

function run(argv) {
  try {
    return JSON.stringify(execute(argv));
  } catch (error) {
    throw new Error("Apple Mail bridge: " + error.message);
  }
}
