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
        woocommerce: "checking",
        wordpress: "checking",
        gemini: "checking",
        notion: "checking"
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
    const isProductsLoading = ref(false);
    const selectedProduct = ref(null);
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
    const isReviewsLoading = ref(false);

    // Notion State
    const notionPages = ref([]);
    const isNotionLoading = ref(false);
    const isSyncRunning = ref(false);
    const syncStatusText = ref("");

    // AI Chat State
    const aiMessages = ref([
      {
        role: "assistant",
        text: "Xin chào! Tôi là Trợ lý AI Khải Hoàn Derma (Gemini 3.8 Flash). Bạn cần hỗ trợ viết mô tả, tư vấn mỹ phẩm hay phân tích báo cáo doanh thu?"
      }
    ]);
    const aiPrompt = ref("");
    const isAiThinking = ref(false);

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
        params.append("per_page", "25");
        const data = await apiCall(`/api/orders?${params.toString()}`);
        orders.value = data.orders || [];
      } catch (err) {
        showToast("Không thể tải danh sách đơn hàng", "error");
      } finally {
        isOrdersLoading.value = false;
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
        params.append("per_page", "30");
        const data = await apiCall(`/api/products?${params.toString()}`);
        products.value = data.products || [];
      } catch (err) {
        showToast("Lỗi khi tìm kiếm sản phẩm", "error");
      } finally {
        isProductsLoading.value = false;
      }
    };

    const onSearchInput = () => {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(() => {
        fetchProducts();
      }, 350);
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
        const data = await apiCall("/api/reviews?status=hold");
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
        reviews.value = reviews.value.filter(r => r.id !== reviewId);
        stats.pending_reviews = Math.max(0, stats.pending_reviews - 1);
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
      } catch (err) {
        showToast(err.message || "Lỗi khi xóa đánh giá", "error");
      }
    };

    // Notion
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
      syncStatusText.value = "Đang khởi động đồng bộ Notion...";
      try {
        const res = await apiCall("/api/notion/sync", { method: "POST" });
        showToast("Đã kích hoạt đồng bộ Notion!", "success");
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
          if (!res.is_running) {
            clearInterval(interval);
            isSyncRunning.value = false;
            showToast("Đồng bộ Notion hoàn tất!", "success");
            fetchNotionQueue();
          }
        } catch (e) {
          clearInterval(interval);
          isSyncRunning.value = false;
        }
      }, 3000);
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

    // Navigation & Tab Switching
    const switchTab = (tab) => {
      haptic("light");
      currentTab.value = tab;
      if (tab === "dashboard") fetchDashboard();
      if (tab === "orders") fetchOrders();
      if (tab === "products") fetchProducts();
      if (tab === "reviews") fetchReviews();
      if (tab === "tools") fetchNotionQueue();
    };

    // Setup & Lifecycle
    onMounted(() => {
      tg.ready();
      tg.expand();
      isDark.value = tg.colorScheme === "dark";
      if (isDark.value) {
        document.body.classList.add("dark-mode");
      }

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
      isProductsLoading,
      editPriceModal,
      reviews,
      isReviewsLoading,
      notionPages,
      isNotionLoading,
      isSyncRunning,
      syncStatusText,
      aiMessages,
      aiPrompt,
      isAiThinking,
      formatMoney,
      formatDate,
      switchTab,
      fetchOrders,
      fetchProducts,
      onSearchInput,
      openEditPriceModal,
      saveProductPrice,
      toggleStock,
      approveReview,
      deleteReview,
      fetchNotionQueue,
      triggerNotionSync,
      askGemini,
      exportExcel
    };
  }
});

app.mount("#app");
