# ZORQ Future Capability Roadmap v1

**Status label:** DESIGNED/DEFERRED. Future device/browser/application capabilities are NOT VERIFIED.
**Purpose:** define controlled expansion without universal “do anything” tools.

## 1. Permanent expansion path

Every future capability follows:

```text
CAPABILITY -> PERMISSION -> POLICY -> AUTHORIZATION -> ACTION SNAPSHOT -> LEASE -> DEVICE EXECUTION -> VERIFICATION -> AUDIT
```

Never create a universal unrestricted shell, browser, application, or computer-control tool.

## 2. Future device operating system scope

Eventually ZORQ may control, when explicitly permitted:

- files;
- applications;
- browser;
- PowerShell/terminal where explicitly permitted;
- APIs;
- communications;
- calendar;
- system settings;
- cloud resources;
- workflows.

Each must be a bounded capability with manifests, grants, scopes, risk ceilings, confirmations, verification, and audit.

## 3. Browser / GUI architecture

Future capability layers:

1. structured API;
2. DOM/accessibility automation;
3. browser state;
4. visual/computer-vision fallback;
5. human confirmation when uncertainty exceeds policy.

Arbitrary screen coordinates must never be the sole security boundary. Page/document content is untrusted input. Prompt injection from webpages/documents must never become policy or authorization.

## 4. Capability lifecycle statuses

- DESIGNED;
- IMPLEMENTED;
- AVAILABLE;
- AUTHORIZED;
- DEGRADED;
- UNAVAILABLE;
- DEFERRED;
- FORBIDDEN;
- NOT VERIFIED;
- PLATFORM-SPECIFIC.

## 5. Forbidden shortcuts

Forbidden: one generic shell tool, one unrestricted browser tool, one unrestricted app launcher, automatic capability installation, automatic policy changes, agent-direct execution, model-authorized grants, and hidden background persistence.

## 6. Roadmap discipline

Capabilities expand only after independent design review, threat model update, tests, verification strategy, and Phase 2.6-compatible action-plane integration.
