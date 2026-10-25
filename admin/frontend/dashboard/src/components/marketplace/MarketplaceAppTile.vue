<script setup lang="ts">
import { Badge, Button, Popover, Tooltip } from 'frappe-ui'
import { computed, ref } from 'vue'

import AppIcon from '@/components/apps/AppIcon.vue'
import GithubMark from '@/components/icons/GithubMark.vue'

interface TileApp {
  name: string
  title: string
  description?: string
  logo_url?: string | null
  repo?: string
  label?: string
  stars?: number | null
  nightly?: boolean
  requires?: { name: string; title: string; logo_url: string }[]
  installed?: boolean
  compatible?: boolean
}

interface Props {
  app: TileApp
}

const props = defineProps<Props>()

const emit = defineEmits(['install'])

const compactNumber = new Intl.NumberFormat('en', { notation: 'compact' })
const isOpen = ref(false)

const hasMeta = computed(() => props.app.stars || props.app.label || props.app.requires?.length)
const isUnavailable = computed(() => props.app.installed || props.app.compatible === false)

const status = computed(() => {
  if (props.app.installed) return 'Installed'
  if (props.app.compatible === false) return 'Needs a newer Frappe version'

  return 'Ready to install'
})

const install = () => {
  isOpen.value = false
  emit('install', props.app)
}
</script>

<template>
  <Popover v-model:open="isOpen" trigger="manual" side="bottom" align="start" :offset="8">
    <template #trigger>
      <article
        class="relative flex flex-col dark:bg-surface-gray-0 p-4 border rounded-6 border-outline-gray-1.5"
      >
        <div class="flex items-center gap-3">
          <AppIcon
            :name="app.name"
            :label="app.title"
            :logo="app.logo_url || ''"
            size="md"
            class="shrink-0"
          />

          <h3 class="flex-1 min-w-0 text-lg-semibold text-ink-gray-8">
            <button
              type="button"
              class="after:absolute after:inset-0 max-w-full after:rounded-6 text-left truncate focus-visible:outline-none focus-visible:after:focus-ring"
              :aria-expanded="isOpen"
              @click="isOpen = !isOpen"
            >
              {{ app.title }}
            </button>
          </h3>

          <Tooltip v-if="app.installed" text="Installed">
            <span
              class="size-4 text-ink-green-7 lucide-circle-check"
              role="img"
              aria-label="Installed"
            />
          </Tooltip>

          <Tooltip v-else-if="app.compatible === false" text="Needs a newer Frappe version">
            <span
              class="size-4 text-ink-amber-6 lucide-triangle-alert"
              role="img"
              aria-label="Needs a newer Frappe version"
            />
          </Tooltip>

          <Button
            v-else
            size="xs"
            variant="ghost"
            icon="lucide-download"
            :tooltip="`Install ${app.title}`"
            :aria-label="`Install ${app.title}`"
            class="z-10 relative -mr-1"
            @click="$emit('install', app)"
          />
        </div>

        <p class="mt-3 text-ink-gray-6 text-sm line-clamp-2">
          {{ app.description || 'No description yet.' }}
        </p>

        <div v-if="hasMeta" class="flex items-center gap-3 mt-auto pt-3 text-ink-gray-5 text-xs">
          <span v-if="app.stars" class="flex items-center gap-1 shrink-0">
            <span class="size-3 lucide-star" />
            {{ compactNumber.format(app.stars) }}
          </span>

          <span v-if="app.label" class="shrink-0">{{ app.label }}</span>

          <Tooltip
            v-for="dependency in app.requires"
            :key="dependency.name"
            :text="`Needs ${dependency.title}`"
          >
            <Badge :label="dependency.title" class="z-10 relative" />
          </Tooltip>
        </div>
      </article>
    </template>

    <template #default>
      <div class="flex flex-col gap-4 p-4 w-96">
        <div class="min-w-0">
          <p class="text-lg-semibold text-ink-gray-8 truncate">{{ app.title }}</p>
          <p v-if="app.label" class="mt-1 text-ink-gray-5 text-sm">{{ app.label }}</p>
        </div>

        <p class="text-ink-gray-7 text-p-sm">{{ app.description || 'No description yet.' }}</p>

        <dl class="flex flex-col gap-2 pt-4 border-t border-outline-gray-2 text-sm">
          <div v-if="app.stars" class="flex justify-between gap-4">
            <dt class="text-ink-gray-5">Stars</dt>
            <dd class="text-ink-gray-8">{{ compactNumber.format(app.stars) }}</dd>
          </div>

          <div v-if="app.requires?.length" class="flex justify-between items-center gap-4">
            <dt class="text-ink-gray-5">Requires</dt>
            <dd class="flex flex-wrap justify-end gap-1">
              <Badge
                v-for="dependency in app.requires"
                :key="dependency.name"
                :label="dependency.title"
              />
            </dd>
          </div>

          <div v-if="app.nightly" class="flex justify-between gap-4">
            <dt class="text-ink-gray-5">Channel</dt>
            <dd class="text-ink-gray-8">Nightly</dd>
          </div>

          <div class="flex justify-between gap-4">
            <dt class="text-ink-gray-5">Status</dt>
            <dd
              :class="{
                'text-ink-green-7': app.installed,
                'text-ink-amber-6': app.compatible === false && !app.installed,
                'text-ink-gray-8': !isUnavailable,
              }"
            >
              {{ status }}
            </dd>
          </div>
        </dl>

        <div class="flex gap-2">
          <Button v-if="app.repo" :href="app.repo" class="flex-1" label="GitHub">
            <template #prefix><GithubMark class="size-4" /></template>
          </Button>

          <Button
            variant="solid"
            class="flex-1"
            :label="app.installed ? 'Installed' : 'Install'"
            :disabled="isUnavailable"
            @click="install"
          >
            <template #prefix><span class="size-4 lucide-download" /></template>
          </Button>
        </div>
      </div>
    </template>
  </Popover>
</template>
