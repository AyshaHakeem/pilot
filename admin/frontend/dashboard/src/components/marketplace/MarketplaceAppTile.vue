<script setup lang="ts">
import { Badge, Button, Tooltip } from 'frappe-ui'
import { computed } from 'vue'

import AppIcon from '@/components/apps/AppIcon.vue'

interface TileApp {
  name: string
  title: string
  description?: string
  logo_url?: string | null
  label?: string
  stars?: number | null
  requires?: { name: string; title: string; logo_url: string }[]
  installed?: boolean
  compatible?: boolean
}

interface Props {
  app: TileApp
}

const props = defineProps<Props>()

defineEmits(['install'])

const compactNumber = new Intl.NumberFormat('en', { notation: 'compact' })

const hasMeta = computed(() => props.app.stars || props.app.label || props.app.requires?.length)
</script>

<template>
  <article
    class="flex flex-col dark:bg-surface-gray-0 p-4 border rounded-6 border-outline-gray-1.5"
  >
    <div class="flex items-center gap-3">
      <AppIcon
        :name="app.name"
        :label="app.title"
        :logo="app.logo_url || ''"
        size="md"
        class="shrink-0"
      />

      <h3 class="flex-1 text-lg-semibold text-ink-gray-8 truncate">{{ app.title }}</h3>

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
        class="-mr-1"
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
        <Badge :label="dependency.title" />
      </Tooltip>
    </div>
  </article>
</template>
