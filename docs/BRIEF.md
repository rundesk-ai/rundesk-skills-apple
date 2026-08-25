# Brief — rundesk-skills-apple

*What this catalog is and why it exists. One screen, and it changes when the catalog does.*

## Story

`rundesk-skills-apple` gives a Rundesk agent guarded access to the Apple apps already signed in on a
Mac — Calendar, Contacts, Mail, and Messages. Each package ships its own command, the guidance for
using it, and offline tests.

Access is local and guarded. The agent talks to the app on this machine through a command that
declares what it needs; nothing is fetched from a cloud account and no credential is copied.

## Why it exists

The data an assistant needs most — what is on the calendar, who somebody is, what was said — already
lives in the apps on the machine. Reaching it through a vendor API would mean a second account, a
second copy of the data, and a credential Rundesk would have to hold.

Going through the local app avoids all three, at the cost of being macOS-only and permission-bound.
That trade is deliberate.

## Users

- Rundesk agents doing assistant work on a Mac, where the owner has already signed into these apps.
- The owner, who grants a package per agent and can see exactly which app each one may reach.

*Sourced from the readme, the environments contract, and the package contract.*

## Scope

- **Covers:** Calendar, Contacts, Mail, and Messages on macOS, each behind its own guarded command,
  with the runtime, configuration, permission, and state contract in `ENVIRONMENTS.md`.
- **Refuses:**
  - Any platform but macOS. These are local app integrations, not vendor APIs.
  - Holding or copying an account credential. The signed-in app is the boundary.
  - Silent access. A package declares what it needs and the owner grants it per agent.
  - General method the default catalog owns, and cloud services that belong in another integration
    catalog.

## External systems

- macOS and the signed-in Apple apps — Calendar, Contacts, Mail, Messages, reached locally.
- macOS privacy permissions — each app's access is granted to the process that asks, per
  `ENVIRONMENTS.md`.
- Rundesk — installs this catalog and grants its packages to named agents.
- GitHub — hosts the repository and serves the release a catalog install fetches.
