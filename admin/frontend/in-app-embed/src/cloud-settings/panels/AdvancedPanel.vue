<script setup lang="ts">
import { useTranslation } from '../translation'
import { getAccountUrl } from '@frappe/cloud-sdk/api'
import { Button, ErrorMessage, SettingsRow } from 'frappe-ui'
import { ref } from 'vue'
import Panel from '../components/Panel.vue'
import { openExternal } from '../external'
import { useErrorMessage, type Store } from '../store'

const __ = useTranslation()
const getErrorMessage = useErrorMessage()

interface Props {
  store: Store
}

defineProps<Props>()

type AccountLink = 'server' | 'billing'

const opening = ref<AccountLink | ''>('')
const openError = ref('')

const open = async (link: AccountLink) => {
  if (opening.value) return

  opening.value = link
  openError.value = ''

  try {
    const links = await getAccountUrl()

    if (!links?.[link]) throw new Error(__('Central is not configured.'))

    openExternal(links[link])
  } catch (exception) {
    openError.value = getErrorMessage(exception)
  } finally {
    opening.value = ''
  }
}
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
          :disabled="Boolean(opening)"
          :label="opening === 'server' ? __('Opening server') : __('Open server')"
          @click="open('server')"
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
          :disabled="Boolean(opening)"
          :label="opening === 'billing' ? __('Opening billing') : __('Manage billing')"
          @click="open('billing')"
        />
      </SettingsRow>
    </div>

    <ErrorMessage :message="openError" class="mt-2" />
  </Panel>
</template>
