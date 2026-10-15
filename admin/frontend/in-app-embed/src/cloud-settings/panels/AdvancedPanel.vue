<script setup lang="ts">
import { useTranslation } from '../translation'
import { Button, ErrorMessage, SettingsRow } from 'frappe-ui'
import Panel from '../components/Panel.vue'
import { openExternal } from '../external'
import type { Store } from '../store'

const __ = useTranslation()

interface Props {
  store: Store
}

const props = defineProps<Props>()

props.store.loadAccountLinks()
</script>

<template>
  <Panel :title="__('Advanced')" :description="__('Deeper controls for your server.')">
    <div class="divide-y divide-outline-gray-1 border-t border-outline-gray-1">
      <SettingsRow
        label-for=""
        :title="__('Open your server')"
        class="flex-col items-start gap-3 sm:flex-row sm:items-center sm:gap-8"
        :description="__('Deploys, scaling, SSH, backups, sites — the full server controls.')"
      >
        <Button
          icon-right="lucide-arrow-up-right"
          :disabled="!store.state.accountLinks"
          :label="__('Open server')"
          @click="openExternal(store.state.accountLinks?.server)"
        />
      </SettingsRow>

      <SettingsRow
        label-for=""
        :title="__('Account & billing')"
        class="flex-col items-start gap-3 sm:flex-row sm:items-center sm:gap-8"
        :description="
          __('Payment methods, invoices, billing email and account settings.')
        "
      >
        <Button
          icon-right="lucide-arrow-up-right"
          :disabled="!store.state.accountLinks"
          :label="__('Manage billing')"
          @click="openExternal(store.state.accountLinks?.billing)"
        />
      </SettingsRow>
    </div>

    <ErrorMessage :message="store.state.accountLinksError" class="mt-2" />
  </Panel>
</template>
