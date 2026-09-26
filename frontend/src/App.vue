<script setup>
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="app-shell">
    <header class="app-nav">
      <div class="app-nav__brand">
        <el-icon :size="20"><Reading /></el-icon>
        <span>企业知识库问答</span>
      </div>
      <nav class="app-nav__links">
        <RouterLink to="/" class="app-nav__link" active-class="is-active">
          <el-icon><ChatDotRound /></el-icon>
          <span>对话</span>
        </RouterLink>
        <RouterLink to="/documents" class="app-nav__link" active-class="is-active">
          <el-icon><FolderOpened /></el-icon>
          <span>文档管理</span>
        </RouterLink>
      </nav>
    </header>

    <div class="app-body">
      <RouterView />
    </div>
  </div>
</template>
