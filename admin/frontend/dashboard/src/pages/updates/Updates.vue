<template>
  <div class="mx-auto max-w-3xl">
    <!-- Header -->
    <div class="flex justify-between items-center gap-3">
      <div>
        <h1 class="font-semibold text-ink-gray-9 text-xl">Updates</h1>
        <p class="mt-1 text-ink-gray-5 text-p-base hidden sm:block">
          App updates across your sites, with backup and recovery.
        </p>
      </div>
      <Button
        variant="subtle"
        size="sm"
        :loading="loading"
        icon-left="lucide-refresh-cw"
        @click="load"
      >
        Refresh
      </Button>
    </div>

    <div v-if="loading && !operations.length" class="flex justify-center mt-16">
      <LoadingText />
    </div>
    <div v-else-if="error" class="mt-4">
      <ErrorMessage :message="error" />
    </div>

    <div
      v-else-if="operations.length"
      class="mt-4 divide-outline-gray-1 divide-y overflow-hidden"
    >
      <RouterLink
        v-for="op in operations"
        :key="op.id"
        :to="{ name: 'UpdateDetail', params: { operationId: op.id } }"
        class="flex items-center gap-3 py-3 no-underline transition-colors"
      >
        <!-- Status icon -->
        <span
          class="place-items-center grid rounded-full size-8 shrink-0"
          :class="rowIcon(op).iconBg"
        >
          <span class="size-4" :class="rowIcon(op).icon" />
        </span>

        <div class="flex-1 min-w-0">
          <span class="font-medium text-ink-gray-9 text-base truncate">{{ opTitle(op) }}</span>
          <p class="mt-0.5 text-ink-gray-5 text-p-sm truncate">{{ subtitle(op) }}</p>
        </div>

        <span class="lucide-chevron-right size-4 text-ink-gray-4 shrink-0" />
      </RouterLink>
    </div>

    <p v-else class="mt-16 text-ink-gray-5 text-sm text-center">No updates yet.</p>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Button, ErrorMessage, LoadingText } from 'frappe-ui'
import { updatesApi } from '@/api/updates'
import { appsSummary, opTitle, pendingActionLabel, stateIcon, stateLabel } from '@/utils/updateFormat'
import { fmtDuration, relativeTime } from '@/utils/taskFormat'

const operations = ref([])
const loading = ref(false)
const error = ref('')

const rowIcon = (op) => stateIcon(op.pending_action ? 'retrying' : op.state)

function subtitle(op) {
  const count = op.sites?.length || 0
  const parts = [
    op.pending_action ? pendingActionLabel(op.pending_action) : stateLabel(op.state),
    `${count} site${count === 1 ? '' : 's'}`,
    appsSummary(op),
    relativeTime(op.started_at || op.created_at),
  ]
  if (op.finished_at && op.started_at) {
    parts.push(`took ${fmtDuration((new Date(op.finished_at) - new Date(op.started_at)) / 1000)}`)
  }
  return parts.filter(Boolean).join(' · ')
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [current, history] = await Promise.all([
      updatesApi.current().catch(() => null),
      updatesApi.list({ limit: 50 }),
    ])
    const rows = history.data || []
    // Pin the active/unresolved operation at the top (it is also in history).
    operations.value = current ? [current, ...rows.filter((op) => op.id !== current.id)] : rows
  } catch (e) {
    error.value = e?.message || 'Could not load updates.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
