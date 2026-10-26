const { createApp, ref, reactive, computed, onMounted, watch } = Vue;

const tg = window.Telegram?.WebApp || {
  ready: () => {},
  expand: () => {},
  close: () => {},
  colorScheme: "light",
  initData: "",
  initDataUnsafe: {},
  HapticFeedback: {
    impactOccurred: () => {},
    notificationOccurred: () => {}
  }
};

const app = createApp({
  setup() {
    const currentTab = ref("dashboard");
    const isDark = ref(false);
    const isLoading = ref(false);
    const toast = reactive({ show: false, message: "", type: "info" });
    
    // Auth & User State
    const user = ref(null);
    const isAuthenticated = ref(false);
    const authError = ref("");
    const devToken = ref(localStorage.getItem("khd_admin_token") || "");

    // Dashboard State
    const stats = reactive({
      today_revenue: 0,
      today_orders: 0,
      month_revenue: 0,
      month_orders: 0,
      pending_reviews: 0,
      notion_queue: 0,
      system_health: {
        woocommerce: "ok",
        wordpress: "ok",
        gemini: "ok",
        notion: "ok"
      }
    });

    // Orders State
    const orders = ref([]);
    const ordersFilter = ref("all");
    const selectedOrder = ref(null);
    const isOrdersLoading = ref(false);

    // Products State
    const products = ref([]);
    const productSearch = ref("");
    const productFilter = ref("all"); // all | instock | outofstock | sale | variable
    const productCategory = ref("");
    const availableCategories = ref([]);
    const currentPage = ref(1);
    const perPage = ref(20);
    const totalProducts = ref(0);
    const totalPages = ref(1);
    const productCounts = reactive({ all: 0, instock: 0, outofstock: 0, sale: 0, variable: 0 });
    const isProductsLoading = ref(false);
    const expandedVariations = reactive({});
    
    const editPriceModal = reactive({
      show: false,
      product: null,
      variation: null,
      regular_price: "",
      sale_price: "",
      isSaving: false
    });

    // Reviews State
    const reviews = ref([]);
    const reviewsFilter = ref("hold"); // hold | approved | all
    const isReviewsLoading = ref(false);

    // Notion State
    const notionPages = ref([]);
    const isNotionLoading = ref(false);
    const isSyncRunning = ref(false);
    const syncStatusText = ref("");

    // Live System & Sync Logs State
    const systemLogs = ref([]);
    const logFilter = ref("");
    const isLogsLoading = ref(false);
    const logContainer = ref(null);

    // AI Chat State
    const aiMessages = ref([
      {
        role: "assistant",
        text: "Xin chào! Tôi là Trợ lý AI Khải Hoàn Derma (Gemini 3.8 Flash). Tôi đã kết nối dữ liệu cửa hàng thời gian thực. Bạn cần tra cứu đơn hàng trong quá khứ, doanh thu, sản phẩm hay tư vấn quản trị?"
      }
    ]);
    const aiPrompt = ref("");
    const isAiThinking = ref(false);

    // System Utilities & Reports State
    const isSystemActionRunning = ref(false);
    const systemReportModal = reactive({
      show: false,
      title: "",
      content: ""
    });

    // Helpers
    const haptic = (type = "light") => {
      try {
        if (type === "success" || type === "error" || type === "warning") {
          tg.HapticFeedback?.notificationOccurred(type);
        } else {
          tg.HapticFeedback?.impactOccurred(type);
        }
      } catch (e) {}
    };

    const showToast = (message, type = "info") => {
      toast.message = message;
      toast.type = type;
      toast.show = true;
      if (type === "success") haptic("success");
      if (type === "error") haptic("error");
      setTimeout(() => {
        toast.show = false;
      }, 3000);
    };

    const formatMoney = (val) => {
      const num = parseFloat(val) || 0;
      return num.toLocaleString("vi-VN") + " ₫";
    };

    const formatDate = (isoStr) => {
      if (!isoStr) return "";
      try {
        const d = new Date(isoStr);
        return d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" }) + " " +
               d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
      } catch (e) {
        return isoStr;
      }
    };

    const openProductLink = (url) => {
      if (!url) return;
      if (tg.openLink) {
        try {
          tg.openLink(url);
          return;
        } catch (e) {}
      }
      window.open(url, "_blank");
    };

    const getOrderStatusLabel = (status) => {
      const map = {
        processing: "Đang xử lý",
        completed: "Hoàn thành",
        "on-hold": "Tạm giữ",
        cancelled: "Đã hủy",
        refunded: "Đã hoàn tiền",
        failed: "Thất bại",
        pending: "Chờ thanh toán"
      };
      return map[status] || status;
    };

    // Theme Logic - Synchronizes both html and body tags
    const applyTheme = (dark) => {
      isDark.value = dark;
      if (dark) {
        document.documentElement.classList.add("dark");
        document.body.classList.add("dark", "dark-mode");
      } else {
        document.documentElement.classList.remove("dark");
        document.body.classList.remove("dark", "dark-mode");
      }
    };

    const toggleTheme = () => {
      const nextTheme = !isDark.value;
      applyTheme(nextTheme);
      localStorage.setItem("khd_theme", nextTheme ? "dark" : "light");
      haptic("light");
    };

    // API Client with Auth headers
    const apiCall = async (url, options = {}) => {
      const headers = {
        "Content-Type": "application/json",
        ...(options.headers || {})
      };

      if (tg.initData) {
        headers["Authorization"] = `tma ${tg.initData}`;
      } else if (devToken.value) {
        headers["X-Admin-Token"] = devToken.value;
      }

      try {
        const res = await fetch(url, { ...options, headers });
        if (res.status === 401) {
          isAuthenticated.value = false;
          authError.value = "Chưa xác thực hoặc không có quyền Admin.";
          throw new Error("Unauthorized");
        }
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || `Lỗi HTTP ${res.status}`);
        }
        return await res.json();
      } catch (err) {
        throw err;
      }
    };

    // Auth Initialization
    const initAuth = async () => {
      isLoading.value = true;
      try {
        const res = await apiCall("/api/auth/verify", { method: "POST" });
        if (res.ok) {
          user.value = res.user;
          isAuthenticated.value = true;
          authError.value = "";
          await fetchDashboard();
        }
      } catch (err) {
        console.warn("Auth verify failed:", err);
        isAuthenticated.value = false;
        authError.value = err.message || "Không thể xác thực Telegram WebApp";
      } finally {
        isLoading.value = false;
      }
    };

    const saveDevToken = () => {
      localStorage.setItem("khd_admin_token", devToken.value);
      initAuth();
    };

    // Dashboard
    const fetchDashboard = async () => {
      try {
        const data = await apiCall("/api/dashboard/stats");
        Object.assign(stats, data);
      } catch (err) {
        console.error("Lỗi tải dashboard:", err);
      }
    };

    // Orders
    const fetchOrders = async () => {
      isOrdersLoading.value = true;
      try {
        const params = new URLSearchParams();
        if (ordersFilter.value !== "all") {
          params.append("status", ordersFilter.value);
        }
        params.append("per_page", "30");
        const data = await apiCall(`/api/orders?${params.toString()}`);
        orders.value = data.orders || [];
      } catch (err) {
        showToast("Không thể tải danh sách đơn hàng", "error");
      } finally {
        isOrdersLoading.value = false;
      }
    };

    const updateOrderStatus = async (orderId, newStatus) => {
      haptic("medium");
      try {
        await apiCall(`/api/orders/${orderId}/status`, {
          method: "POST",
          body: JSON.stringify({ status: newStatus })
        });
        showToast(`Đã chuyển đơn #${orderId} sang ${getOrderStatusLabel(newStatus)}!`, "success");
        if (selectedOrder.value && selectedOrder.value.id === orderId) {
          selectedOrder.value.status = newStatus;
        }
        const found = orders.value.find(o => o.id === orderId);
        if (found) found.status = newStatus;
        fetchDashboard();
      } catch (err) {
        showToast(err.message || "Lỗi cập nhật trạng thái đơn hàng", "error");
      }
    };

    // Products
    let searchDebounceTimer = null;
    const fetchProducts = async () => {
      isProductsLoading.value = true;
      try {
        const params = new URLSearchParams();
        if (productSearch.value.trim()) {
          params.append("search", productSearch.value.trim());
        }
        if (productFilter.value !== "all") {
          params.append("filter", productFilter.value);
        }
        if (productCategory.value) {
          params.append("category", productCategory.value);
        }
        params.append("page", currentPage.value.toString());
        params.append("per_page", perPage.value.toString());

        const data = await apiCall(`/api/products?${params.toString()}`);
        products.value = data.products || [];
        totalProducts.value = data.total || 0;
        totalPages.value = data.total_pages || 1;
        if (data.counts) {
          Object.assign(productCounts, data.counts);
        }
        if (data.categories && Array.isArray(data.categories)) {
          availableCategories.value = data.categories;
        }
      } catch (err) {
        showToast("Lỗi khi tìm kiếm sản phẩm", "error");
      } finally {
        isProductsLoading.value = false;
      }
    };

    const onSearchInput = () => {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(() => {
        currentPage.value = 1;
        fetchProducts();
      }, 300);
    };

    const goToPage = (page) => {
      if (page < 1 || page > totalPages.value) return;
      haptic("light");
      currentPage.value = page;
      fetchProducts();
      window.scrollTo({ top: 0, behavior: "smooth" });
    };

    const toggleVariationsExpand = (productId) => {
      haptic("light");
      expandedVariations[productId] = !expandedVariations[productId];
    };

    const openEditPriceModal = (product, variation = null) => {
      haptic("light");
      editPriceModal.product = product;
      editPriceModal.variation = variation;
      editPriceModal.regular_price = variation ? (variation.regular_price || "") : (product.regular_price || "");
      editPriceModal.sale_price = variation ? (variation.sale_price || "") : (product.sale_price || "");
      editPriceModal.show = true;
    };

    const saveProductPrice = async () => {
      if (!editPriceModal.product) return;
      editPriceModal.isSaving = true;
      haptic("medium");
      try {
        const pId = editPriceModal.product.id;
        const payload = {
          regular_price: editPriceModal.regular_price,
          sale_price: editPriceModal.sale_price,
          variation_id: editPriceModal.variation ? editPriceModal.variation.id : null
        };
        const res = await apiCall(`/api/products/${pId}/price`, {
          method: "POST",
          body: JSON.stringify(payload)
        });
        showToast(res.message || "Cập nhật giá thành công!", "success");
        editPriceModal.show = false;
        fetchProducts();
      } catch (err) {
        showToast(err.message || "Lỗi cập nhật giá", "error");
      } finally {
        editPriceModal.isSaving = false;
      }
    };

    const toggleStock = async (product, variation = null) => {
      haptic("medium");
      const currentStock = variation ? variation.stock_status : product.stock_status;
      const newStock = currentStock === "instock" ? "outofstock" : "instock";
      try {
        const pId = product.id;
        const payload = {
          stock_status: newStock,
          variation_id: variation ? variation.id : null
        };
        await apiCall(`/api/products/${pId}/stock`, {
          method: "POST",
          body: JSON.stringify(payload)
        });
        if (variation) {
          variation.stock_status = newStock;
        } else {
          product.stock_status = newStock;
          if (newStock === "instock") {
            productCounts.instock = (productCounts.instock || 0) + 1;
            productCounts.outofstock = Math.max(0, (productCounts.outofstock || 0) - 1);
          } else {
            productCounts.instock = Math.max(0, (productCounts.instock || 0) - 1);
            productCounts.outofstock = (productCounts.outofstock || 0) + 1;
          }
        }
        showToast(`Đã chuyển thành ${newStock === 'instock' ? 'Còn hàng' : 'Hết hàng'}`, "success");
      } catch (err) {
        showToast("Không thể đổi trạng thái tồn kho", "error");
      }
    };

    // Reviews
    const fetchReviews = async () => {
      isReviewsLoading.value = true;
      try {
        const data = await apiCall(`/api/reviews?status=${reviewsFilter.value}`);
        reviews.value = data.reviews || [];
      } catch (err) {
        showToast("Lỗi khi tải danh sách đánh giá", "error");
      } finally {
        isReviewsLoading.value = false;
      }
    };

    const approveReview = async (reviewId) => {
      haptic("medium");
      try {
        await apiCall(`/api/reviews/${reviewId}/approve`, { method: "POST" });
        showToast("Đã phê duyệt đánh giá thành công!", "success");
        if (reviewsFilter.value === "hold") {
          reviews.value = reviews.value.filter(r => r.id !== reviewId);
        } else {
          const found = reviews.value.find(r => r.id === reviewId);
          if (found) found.status = "approved";
        }
        stats.pending_reviews = Math.max(0, stats.pending_reviews - 1);
        fetchDashboard();
      } catch (err) {
        showToast(err.message || "Lỗi khi duyệt đánh giá", "error");
      }
    };

    const deleteReview = async (reviewId) => {
      haptic("heavy");
      if (!confirm("Bạn có chắc chắn muốn xóa đánh giá này không?")) return;
      try {
        await apiCall(`/api/reviews/${reviewId}/delete`, { method: "POST" });
        showToast("Đã xóa đánh giá!", "success");
        reviews.value = reviews.value.filter(r => r.id !== reviewId);
        stats.pending_reviews = Math.max(0, stats.pending_reviews - 1);
        fetchDashboard();
      } catch (err) {
        showToast(err.message || "Lỗi khi xóa đánh giá", "error");
      }
    };

    // Notion Sync & Live Logs
    const fetchLogs = async () => {
      isLogsLoading.value = true;
      try {
        const param = logFilter.value ? `&filter_tag=${encodeURIComponent(logFilter.value)}` : "";
        const data = await apiCall(`/api/system/logs?lines=60${param}`);
        systemLogs.value = data.logs || [];
        scrollLogsToBottom();
      } catch (err) {
        console.warn("Lỗi tải log hệ thống:", err);
      } finally {
        isLogsLoading.value = false;
      }
    };

    const clearLogView = () => {
      systemLogs.value = [];
      showToast("Đã xóa màn hình xem nhật ký", "info");
    };

    const scrollLogsToBottom = () => {
      setTimeout(() => {
        if (logContainer.value) {
          logContainer.value.scrollTop = logContainer.value.scrollHeight;
        }
      }, 60);
    };

    const fetchNotionQueue = async () => {
      isNotionLoading.value = true;
      try {
        const data = await apiCall("/api/notion/pages");
        notionPages.value = data.pages || [];
        stats.notion_queue = notionPages.value.length;
      } catch (err) {
        showToast("Không thể tải danh sách bài viết Notion", "error");
      } finally {
        isNotionLoading.value = false;
      }
    };

    const triggerNotionSync = async () => {
      haptic("heavy");
      isSyncRunning.value = true;
      syncStatusText.value = "Đang kết nối Notion và kiểm tra bài viết...";
      try {
        await apiCall("/api/notion/sync", { method: "POST" });
        showToast("Đã kích hoạt đồng bộ Notion!", "success");
        await fetchLogs();
        pollSyncStatus();
      } catch (err) {
        showToast(err.message || "Lỗi khởi động đồng bộ Notion", "error");
        isSyncRunning.value = false;
      }
    };

    const pollSyncStatus = () => {
      const interval = setInterval(async () => {
        try {
          const res = await apiCall("/api/notion/status");
          syncStatusText.value = res.current_status || "Đang xử lý...";
          await fetchLogs();
          if (!res.is_running) {
            clearInterval(interval);
            isSyncRunning.value = false;
            showToast("Đồng bộ Notion hoàn tất!", "success");
            fetchNotionQueue();
            fetchDashboard();
          }
        } catch (e) {
          clearInterval(interval);
          isSyncRunning.value = false;
        }
      }, 2500);
    };

    const sendAiPrompt = (text) => {
      aiPrompt.value = text;
      askGemini();
    };

    // Gemini AI Chat
    const askGemini = async () => {
      if (!aiPrompt.value.trim() || isAiThinking.value) return;
      haptic("medium");
      const question = aiPrompt.value.trim();
      aiMessages.value.push({ role: "user", text: question });
      aiPrompt.value = "";
      isAiThinking.value = true;

      try {
        const res = await apiCall("/api/ai/ask", {
          method: "POST",
          body: JSON.stringify({ prompt: question })
        });
        aiMessages.value.push({ role: "assistant", text: res.answer || "Đã xử lý." });
      } catch (err) {
        aiMessages.value.push({
          role: "assistant",
          text: "Xin lỗi, đã xảy ra lỗi kết nối tới Gemini API: " + (err.message || "Vui lòng thử lại sau.")
        });
      } finally {
        isAiThinking.value = false;
      }
    };

    // Exports
    const exportExcel = (type = "today") => {
      haptic("medium");
      const tokenHeader = tg.initData ? `?tgWebAppInitData=${encodeURIComponent(tg.initData)}` : (devToken.value ? `?admin_token=${devToken.value}` : "");
      const url = `/api/export/orders-${type}${tokenHeader}`;
      window.open(url, "_blank");
    };

    const exportProductCatalog = () => {
      haptic("medium");
      const tokenHeader = tg.initData ? `?tgWebAppInitData=${encodeURIComponent(tg.initData)}` : (devToken.value ? `?admin_token=${devToken.value}` : "");
      const url = `/api/export/products-catalog${tokenHeader}`;
      window.open(url, "_blank");
    };

    // Product Deletion
    const deleteProduct = async (product) => {
      haptic("heavy");
      if (!confirm(`Bạn có chắc muốn chuyển sản phẩm "${product.name}" vào thùng rác không?`)) return;
      try {
        await apiCall(`/api/products/${product.id}`, { method: "DELETE" });
        showToast("Đã chuyển sản phẩm vào thùng rác!", "success");
        products.value = products.value.filter(p => p.id !== product.id);
        totalProducts.value = Math.max(0, totalProducts.value - 1);
        productCounts.all = Math.max(0, (productCounts.all || 0) - 1);
      } catch (err) {
        showToast(err.message || "Lỗi khi xóa sản phẩm", "error");
      }
    };

    // System Utilities & SEO Actions
    const runPingTest = async () => {
      haptic("medium");
      isSystemActionRunning.value = true;
      try {
        const res = await apiCall("/api/system/ping");
        systemReportModal.title = "Trạng Thái Kết Nối (Ping & Latency)";
        systemReportModal.content = res.html || "Hệ thống hoạt động bình thường.";
        systemReportModal.show = true;
        showToast("Kiểm tra kết nối hoàn tất!", "success");
      } catch (err) {
        showToast(err.message || "Lỗi khi ping hệ thống", "error");
      } finally {
        isSystemActionRunning.value = false;
      }
    };

    const refreshCache = async () => {
      haptic("medium");
      isSystemActionRunning.value = true;
      try {
        const res = await apiCall("/api/system/cache-refresh", { method: "POST" });
        showToast(res.message || "Đã làm mới cache sản phẩm!", "success");
        fetchProducts();
      } catch (err) {
        showToast(err.message || "Lỗi làm mới cache", "error");
      } finally {
        isSystemActionRunning.value = false;
      }
    };

    const clearReviewsCache = async () => {
      haptic("medium");
      isSystemActionRunning.value = true;
      try {
        const res = await apiCall("/api/system/reviews-cache-clear", { method: "POST" });
        showToast(res.message || "Đã xóa cache đánh giá!", "success");
        fetchReviews();
        fetchDashboard();
      } catch (err) {
        showToast(err.message || "Lỗi xóa cache đánh giá", "error");
      } finally {
        isSystemActionRunning.value = false;
      }
    };

    const fetchGoogleReport = async (type = "traffic") => {
      haptic("medium");
      isSystemActionRunning.value = true;
      try {
        const res = await apiCall(`/api/reports/google-seo?type=${type}`);
        systemReportModal.title = type === "traffic" ? "Báo Cáo Google Traffic (28 ngày)" : "Từ Khóa Google Search Console";
        systemReportModal.content = res.html_report || "Không có dữ liệu báo cáo.";
        systemReportModal.show = true;
        showToast("Đã tải báo cáo SEO!", "success");
      } catch (err) {
        showToast(err.message || "Lỗi tải báo cáo SEO", "error");
      } finally {
        isSystemActionRunning.value = false;
      }
    };

    // Navigation & Tab Switching
    const switchTab = (tab) => {
      haptic("light");
      currentTab.value = tab;
      if (tab === "dashboard") fetchDashboard();
      if (tab === "orders") fetchOrders();
      if (tab === "products") fetchProducts();
      if (tab === "reviews") fetchReviews();
      if (tab === "tools") {
        fetchNotionQueue();
        fetchLogs();
      }
    };

    const refreshCurrentTab = () => {
      haptic("medium");
      switchTab(currentTab.value);
      showToast("Đã làm mới dữ liệu!", "info");
    };

    // Setup & Lifecycle
    onMounted(() => {
      tg.ready();
      tg.expand();

      // Check saved theme or fallback to Telegram colorScheme
      const savedTheme = localStorage.getItem("khd_theme");
      if (savedTheme) {
        applyTheme(savedTheme === "dark");
      } else {
        applyTheme(tg.colorScheme === "dark");
      }

      try {
        tg.onEvent?.("themeChanged", () => {
          if (!localStorage.getItem("khd_theme")) {
            applyTheme(tg.colorScheme === "dark");
          }
        });
      } catch (e) {}

      initAuth();
    });

    return {
      currentTab,
      isDark,
      isLoading,
      toast,
      user,
      isAuthenticated,
      authError,
      devToken,
      saveDevToken,
      stats,
      orders,
      ordersFilter,
      selectedOrder,
      isOrdersLoading,
      products,
      productSearch,
      productFilter,
      productCategory,
      availableCategories,
      openProductLink,
      currentPage,
      perPage,
      totalProducts,
      totalPages,
      productCounts,
      isProductsLoading,
      expandedVariations,
      editPriceModal,
      reviews,
      reviewsFilter,
      isReviewsLoading,
      notionPages,
      isNotionLoading,
      isSyncRunning,
      syncStatusText,
      systemLogs,
      logFilter,
      isLogsLoading,
      logContainer,
      fetchLogs,
      clearLogView,
      aiMessages,
      aiPrompt,
      isAiThinking,
      isSystemActionRunning,
      systemReportModal,
      formatMoney,
      formatDate,
      getOrderStatusLabel,
      toggleTheme,
      switchTab,
      refreshCurrentTab,
      fetchOrders,
      updateOrderStatus,
      fetchProducts,
      onSearchInput,
      goToPage,
      toggleVariationsExpand,
      openEditPriceModal,
      saveProductPrice,
      toggleStock,
      deleteProduct,
      fetchReviews,
      approveReview,
      deleteReview,
      fetchNotionQueue,
      triggerNotionSync,
      askGemini,
      sendAiPrompt,
      exportExcel,
      exportProductCatalog,
      runPingTest,
      refreshCache,
      clearReviewsCache,
      fetchGoogleReport
    };

  }
});

app.mount("#app");
