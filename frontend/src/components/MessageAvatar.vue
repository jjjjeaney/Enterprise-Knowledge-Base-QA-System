<script setup>
defineProps({
  user: { type: Boolean, default: false },
})
</script>

<template>
  <el-avatar
    :size="32"
    class="bubble-avatar"
    :class="user ? 'is-user' : 'is-ai'"
  >
    <el-icon :size="18">
      <UserFilled v-if="user" />
      <!-- Element Plus 没有机器人图标，这里内联一个：描边走 currentColor，
           沿用 .bubble-avatar.is-ai 的品牌蓝。 -->
      <svg
        v-else
        viewBox="0 0 24 24"
        width="1em"
        height="1em"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="M12 5.5v3.5" />
        <circle cx="12" cy="4.2" r="1.5" fill="currentColor" stroke="none" />
        <rect x="4" y="9" width="16" height="12" rx="3.5" />
        <circle cx="9.2" cy="14.5" r="1.4" fill="currentColor" stroke="none" />
        <circle cx="14.8" cy="14.5" r="1.4" fill="currentColor" stroke="none" />
        <path d="M9.5 18h5" />
      </svg>
    </el-icon>
  </el-avatar>
</template>
