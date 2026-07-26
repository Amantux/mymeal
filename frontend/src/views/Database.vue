<script setup>
// myMeal connects to Postgres by auto-provisioning against the Shared PostgreSQL
// add-on (see app/pg_provision.py) rather than an in-app migrate, so this page is
// informational — it explains the database story to match the companion apps'
// Database utility.
</script>

<template>
  <div class="page-head"><h1>Database</h1></div>

  <div class="card">
    <h2>Database backend</h2>
    <p class="muted">
      myMeal runs on its built-in <strong>SQLite</strong> database by default — a single file under the
      add-on's data directory, backed up automatically by Home Assistant. This is the right choice for most
      households and needs no configuration.
    </p>
  </div>

  <div class="card">
    <h2>Shared PostgreSQL</h2>
    <p class="muted">
      To run on <strong>PostgreSQL</strong> — e.g. to share one database server with the companion apps —
      install the <strong>Shared PostgreSQL</strong> Home Assistant add-on and enable
      <code>use_shared_postgres</code> in myMeal's add-on options. On startup myMeal discovers the add-on,
      provisions its own database, and comes up on Postgres automatically. No manual copy step is needed
      (unlike the file-based apps, which migrate their SQLite data across).
    </p>
    <p class="muted" style="font-size:.85rem">
      If anything goes wrong provisioning, myMeal stays on SQLite so it never blocks startup — check the
      add-on log for details.
    </p>
  </div>
</template>
