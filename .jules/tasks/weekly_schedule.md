# Elite Weekly Schedule 📅

This schedule coordinates the elite agents to ensure the `vault-memory` codebase remains high-performance, secure, and strategically aligned.

## Overview

| Agent | Frequency | Day (Suggested) | Primary Goal |
|-------|-----------|-----------------|--------------|
| **Sentinel** 🛡️⚡ | Weekly | Monday | Security Audit & Performance Boost |
| **Bridge** 🌉 | Weekly | Wednesday | Ecosystem Sync & Plugin Health |
| **Conductor** 🎹 | Weekly | Friday | Sprint Transition & Roadmap Sync |

## Detailed Rituals

### Monday: The Sentinel 🛡️⚡
- **Agent:** Sentinel
- **Goal:** Start the week by hardening the core.
- **Ritual:**
  1. Perform a "Red-Team Audit" of any changes made in the previous week.
  2. Identify and implement one performance bottleneck fix (the "Bolt" task).
  3. Verify DI and Lite Mode integrity.
- **Artifact:** A PR with prefix `🛡️ Sentinel:` and a "Critical Learning" entry in `.jules/sentinel.md`.

### Wednesday: The Bridge 🌉
- **Agent:** Bridge
- **Goal:** Ensure the ecosystem is cohesive.
- **Ritual:**
  1. Audit MCP tool parity across Daemon, CLI, and Plugin.
  2. Build and verify the Obsidian Plugin.
  3. Update CLI commands if the Daemon API has drifted.
- **Artifact:** A PR with prefix `🌉 Bridge:` and any necessary TypeScript/CLI updates.

### Friday: The Conductor 🎹
- **Agent:** Conductor
- **Goal:** Close the loop and plan for next week.
- **Ritual:**
  1. Retire completed sprints to the archive.
  2. Update `CONDUCTOR_MASTER.md` and initialize new sprint tracks.
  3. Sync `STATE.md` and bump the version in `pyproject.toml` if milestones were hit.
- **Artifact:** A PR with prefix `🎹 Conductor:` and updated strategic documentation.

---

## Instructions for Jules
When assigned a "Weekly Ritual" task, load the corresponding agent persona from `.jules/agents/` and follow the "Weekly Ritual Tasks" section of that file.
