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

    // Store Selection State (Multi-site: MCP Khải Hoàn)
    const selectedStore = ref("all"); // "all" | "derma" | "pharma"
    const availableSites = ref([
      { id: "all", name: "Toàn hệ thống", short_name: "MCP", badge: "MCP" },
      { id: "derma", name: "Khải Hoàn Derma", short_name: "Derma", badge: "Derma" },
      { id: "pharma", name: "Khải Hoàn Pharma", short_name: "Pharma", badge: "Pharma" }
    ]);

    const setStoreFilter = (storeKey) => {
      selectedStore.value = storeKey;
      haptic("light");
      if (currentTab.value === "dashboard") {
        fetchDashboard();
      } else if (currentTab.value === "orders") {
        fetchOrders();
      } else if (currentTab.value === "products") {
        currentPage.value = 1;
        fetchProducts();
      } else if (currentTab.value === "tools") {
        fetchNotionQueue();
      }
    };

    // Dashboard State
    const stats = reactive({
      today_revenue: 0,
      today_orders: 0,
      month_revenue: 0,
      month_orders: 0,
      pending_reviews: 0,
      notion_queue: 0,
      breakdown: {
        derma: { today_revenue: 0, today_orders: 0, month_revenue: 0, month_orders: 0 },
        pharma: { today_revenue: 0, today_orders: 0, month_revenue: 0, month_orders: 0 }
      },
      system_health: {
        woocommerce: "ok",
        wordpress: "ok",
        derma: "ok",
        pharma: "ok",
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

    const editImagesModal = reactive({
      show: false,
      product: null,
      images: [],
      isUploading: false,
      isSavingOrder: false,
      uploadAsFeatured: false,
      orderChanged: false,
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
    const aiChatContainer = ref(null);
    const aiMessages = ref([
      {
        role: "assistant",
        text: "Xin chào! Tôi là Trợ lý AI MCP Khải Hoàn (Gemini 3.8 Flash). Tôi đã kết nối dữ liệu 2 website Khải Hoàn Derma & Khải Hoàn Pharma thời gian thực. Bạn cần tra cứu đơn hàng trong quá khứ, doanh thu, sản phẩm hay tư vấn quản trị?"
      }
    ]);
    const aiPrompt = ref("");
    const isAiThinking = ref(false);

    const scrollAiChatToBottom = () => {
      setTimeout(() => {
        if (aiChatContainer.value) {
          aiChatContainer.value.scrollTop = aiChatContainer.value.scrollHeight;
        }
      }, 50);
    };

    const clearAiChat = () => {
      aiMessages.value = [
        {
          role: "assistant",
          text: "Đã làm mới cuộc hội thoại. Bạn cần tra cứu đơn hàng trong quá khứ, doanh thu, sản phẩm hay tư vấn quản trị?"
        }
      ];
      showToast("Đã làm mới hộp thoại AI", "info");
      scrollAiChatToBottom();
    };

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
      let msgStr = "";
      if (typeof message === "string") {
        msgStr = message;
      } else if (message instanceof Error) {
        msgStr = message.message || "Lỗi thao tác";
      } else if (message && typeof message === "object") {
        if (typeof message.detail === "string") {
          msgStr = message.detail;
        } else if (Array.isArray(message.detail)) {
          msgStr = message.detail.map(d => (d && typeof d === "object" ? (d.msg || JSON.stringify(d)) : String(d))).join("; ");
        } else if (typeof message.message === "string") {
          msgStr = message.message;
        } else {
          try {
            msgStr = JSON.stringify(message);
          } catch (e) {
            msgStr = String(message);
          }
        }
      } else {
        msgStr = String(message || "Thông báo");
      }
      if (msgStr === "[object Object]" || !msgStr.trim()) {
        msgStr = "Thao tác không thành công. Vui lòng kiểm tra lại.";
      }
      toast.message = msgStr;
      toast.type = type;
      toast.show = true;
      if (type === "success") haptic("success");
      if (type === "error") haptic("error");
      setTimeout(() => {
        toast.show = false;
      }, 3500);
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
          let errMsg = `Lỗi HTTP ${res.status}`;
          if (typeof errData.detail === "string") {
            errMsg = errData.detail;
          } else if (Array.isArray(errData.detail) && errData.detail.length > 0) {
            errMsg = errData.detail.map(d => {
              if (typeof d === "string") return d;
              if (d && typeof d === "object") {
                const loc = Array.isArray(d.loc) ? d.loc.filter(x => x !== "body").join(".") : "";
                return (loc ? `${loc}: ` : "") + (d.msg || JSON.stringify(d));
              }
              return String(d);
            }).join("; ");
          } else if (errData.detail && typeof errData.detail === "object") {
            errMsg = errData.detail.message || JSON.stringify(errData.detail);
          } else if (errData.message) {
            errMsg = errData.message;
          }
          throw new Error(errMsg);
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
        const data = await apiCall(`/api/dashboard/stats?site=${selectedStore.value}`);
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
        params.append("site", selectedStore.value);
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
        const found = orders.value.find(o => o.id === orderId);
        const siteKey = found?._site_key || "derma";
        await apiCall(`/api/orders/${orderId}/status?site_key=${siteKey}`, {
          method: "POST",
          body: JSON.stringify({ status: newStatus, site_key: siteKey })
        });
        showToast(`Đã chuyển đơn #${orderId} sang ${getOrderStatusLabel(newStatus)}!`, "success");
        if (selectedOrder.value && selectedOrder.value.id === orderId) {
          selectedOrder.value.status = newStatus;
        }
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
        params.append("site", selectedStore.value);
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

      const regRaw = editPriceModal.regular_price !== null && editPriceModal.regular_price !== undefined 
        ? String(editPriceModal.regular_price).trim() 
        : "";
      const saleRaw = editPriceModal.sale_price !== null && editPriceModal.sale_price !== undefined 
        ? String(editPriceModal.sale_price).trim() 
        : "";

      const regNum = parseFloat(regRaw);
      const saleNum = parseFloat(saleRaw);

      if (!regRaw || isNaN(regNum) || regNum < 0) {
        showToast("Vui lòng nhập giá thường hợp lệ (>= 0đ)", "error");
        return;
      }

      if (saleRaw && !isNaN(saleNum) && saleNum > 0) {
        if (saleNum >= regNum) {
          showToast("Giá khuyến mãi phải thấp hơn giá bán thông thường!", "error");
          return;
        }
      }

      editPriceModal.isSaving = true;
      haptic("medium");
      try {
        const pId = editPriceModal.product.id;
        const siteKey = editPriceModal.product._site_key || "derma";
        const payload = {
          regular_price: regRaw,
          sale_price: saleRaw,
          variation_id: editPriceModal.variation ? editPriceModal.variation.id : null,
          site_key: siteKey
        };
        const res = await apiCall(`/api/products/${pId}/price?site_key=${siteKey}`, {
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

    const openEditImagesModal = (product) => {
      haptic("light");
      editImagesModal.product = product;
      editImagesModal.images = (product.images || []).map(img => ({ ...img }));
      editImagesModal.uploadAsFeatured = false;
      editImagesModal.orderChanged = false;
      editImagesModal.show = true;
    };

    const handleImageFileSelected = async (event) => {
      const files = event.target.files;
      if (!files || files.length === 0) return;

      editImagesModal.isUploading = true;
      haptic("medium");

      try {
        const fileList = Array.from(files);
        const encodedImages = [];

        for (const file of fileList) {
          if (!file.type.startsWith("image/")) {
            showToast(`File "${file.name}" không phải định dạng ảnh hợp lệ.`, "warning");
            continue;
          }
          if (file.size > 10 * 1024 * 1024) {
            showToast(`Ảnh "${file.name}" quá lớn (> 10MB). Vui lòng chọn ảnh nhỏ hơn.`, "warning");
            continue;
          }

          const base64Data = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = (e) => reject(e);
            reader.readAsDataURL(file);
          });

          encodedImages.push({
            filename: file.name,
            data: base64Data
          });
        }

        if (encodedImages.length === 0) {
          editImagesModal.isUploading = false;
          event.target.value = "";
          return;
        }

        const pId = editImagesModal.product.id;
        const siteKey = editImagesModal.product._site_key || "derma";
        const payload = {
          images: encodedImages,
          site_key: siteKey,
          as_featured: editImagesModal.uploadAsFeatured
        };

        const res = await apiCall(`/api/products/${pId}/images/upload`, {
          method: "POST",
          body: JSON.stringify(payload)
        });

        showToast(res.message || "Tải ảnh lên thành công!", "success");
        if (res.images) {
          editImagesModal.images = res.images;
          editImagesModal.product.images = res.images;
        }
        editImagesModal.orderChanged = false;
        fetchProducts();
      } catch (err) {
        showToast(err.message || "Lỗi tải ảnh lên", "error");
      } finally {
        editImagesModal.isUploading = false;
        event.target.value = "";
      }
    };

    const moveImagePosition = (index, direction) => {
      haptic("light");
      const targetIndex = index + direction;
      if (targetIndex < 0 || targetIndex >= editImagesModal.images.length) return;

      const item = editImagesModal.images.splice(index, 1)[0];
      editImagesModal.images.splice(targetIndex, 0, item);
      editImagesModal.orderChanged = true;
    };

    const setFeaturedImage = (index) => {
      haptic("medium");
      if (index === 0) return;
      const item = editImagesModal.images.splice(index, 1)[0];
      editImagesModal.images.unshift(item);
      editImagesModal.orderChanged = true;
      saveImagesOrder();
    };

    const saveImagesOrder = async () => {
      if (!editImagesModal.product) return;
      editImagesModal.isSavingOrder = true;
      haptic("medium");
      try {
        const pId = editImagesModal.product.id;
        const siteKey = editImagesModal.product._site_key || "derma";
        const imageIds = editImagesModal.images.map(img => img.id).filter(Boolean);

        const res = await apiCall(`/api/products/${pId}/images`, {
          method: "PUT",
          body: JSON.stringify({
            image_ids: imageIds,
            site_key: siteKey
          })
        });

        showToast(res.message || "Đã lưu thứ tự ảnh thành công!", "success");
        if (res.images) {
          editImagesModal.images = res.images;
          editImagesModal.product.images = res.images;
        }
        editImagesModal.orderChanged = false;
        fetchProducts();
      } catch (err) {
        showToast(err.message || "Lỗi lưu thứ tự ảnh", "error");
      } finally {
        editImagesModal.isSavingOrder = false;
      }
    };

    const deleteProductImage = async (img, index) => {
      haptic("medium");
      const confirmDelete = window.confirm(`Bạn có chắc muốn xóa ảnh #${img.id || (index + 1)} khỏi sản phẩm?`);
      if (!confirmDelete) return;

      const deleteMediaFromLibrary = window.confirm(`Bạn có muốn xóa vĩnh viễn file ảnh này khỏi Media Library trên WordPress không?\n(Bấm OK để xóa hẳn khỏi website, Cancel để chỉ gỡ khỏi sản phẩm)`);

      try {
        const pId = editImagesModal.product.id;
        const siteKey = editImagesModal.product._site_key || "derma";
        const res = await apiCall(`/api/products/${pId}/images/${img.id}?site_key=${siteKey}&delete_media=${deleteMediaFromLibrary}`, {
          method: "DELETE"
        });

        showToast(res.message || "Đã xóa ảnh thành công!", "success");
        if (res.images) {
          editImagesModal.images = res.images;
          editImagesModal.product.images = res.images;
        } else {
          editImagesModal.images.splice(index, 1);
        }
        editImagesModal.orderChanged = false;
        fetchProducts();
      } catch (err) {
        showToast(err.message || "Lỗi xóa ảnh", "error");
      }
    };

    const toggleStock = async (product, variation = null) => {
      haptic("medium");
      const currentStock = variation ? variation.stock_status : product.stock_status;
      const newStock = currentStock === "instock" ? "outofstock" : "instock";
      const siteKey = product._site_key || "derma";
      try {
        const pId = product.id;
        const payload = {
          stock_status: newStock,
          variation_id: variation ? variation.id : null,
          site_key: siteKey
        };
        await apiCall(`/api/products/${pId}/stock?site_key=${siteKey}`, {
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
        const data = await apiCall(`/api/notion/pages?site=${selectedStore.value}`);
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
      syncStatusText.value = `Đang kết nối Notion và kiểm tra bài viết (${selectedStore.value})...`;
      try {
        await apiCall(`/api/notion/sync?site=${selectedStore.value}`, { method: "POST" });
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

    // Markdown & HTML Formatter for AI Chat
    const parseMarkdownFallback = (rawText) => {
      if (!rawText) return "";

      const lines = rawText.split(/\r?\n/);
      const output = [];
      let inTable = false;
      let inUl = false;
      let inOl = false;

      const closeLists = () => {
        if (inUl) {
          output.push("</ul>");
          inUl = false;
        }
        if (inOl) {
          output.push("</ol>");
          inOl = false;
        }
      };

      const closeTable = () => {
        if (inTable) {
          output.push("</tbody></table></div>");
          inTable = false;
        }
      };

      const formatInline = (text) => {
        if (!text) return "";
        return text
          // Bold **text**
          .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
          // Italic *text*
          .replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, "<em>$1</em>")
          // Inline code `code`
          .replace(/`([^`]+)`/g, "<code>$1</code>")
          // Status badge highlights [Đang xử lý], [Tạm giữ], [Hoàn thành]
          .replace(/\[(Đang xử lý|Processing)\]/gi, '<span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-500/15 text-amber-600 dark:text-amber-400">Đang xử lý</span>')
          .replace(/\[(Tạm giữ|On-hold)\]/gi, '<span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold bg-purple-500/15 text-purple-600 dark:text-purple-400">Tạm giữ</span>')
          .replace(/\[(Hoàn thành|Completed)\]/gi, '<span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">Hoàn thành</span>')
          .replace(/\[(Đã hủy|Cancelled)\]/gi, '<span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-500/15 text-rose-600 dark:text-rose-400">Đã hủy</span>');
      };

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const trimmed = line.trim();

        // 1. Markdown Table
        if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
          closeLists();
          const cells = trimmed.slice(1, -1).split("|").map(c => c.trim());

          // Separator line | :--- | :---: |
          if (cells.every(c => /^:?-+:?$/.test(c))) {
            continue;
          }

          if (!inTable) {
            inTable = true;
            output.push('<div class="table-container"><table><thead><tr>');
            cells.forEach(c => {
              output.push(`<th>${formatInline(c)}</th>`);
            });
            output.push('</tr></thead><tbody>');
          } else {
            output.push('<tr>');
            cells.forEach((c, idx) => {
              const isCenter = idx === 0 || /^\d+$/.test(c);
              const alignClass = isCenter ? ' class="text-center"' : '';
              output.push(`<td${alignClass}>${formatInline(c)}</td>`);
            });
            output.push('</tr>');
          }
          continue;
        } else {
          closeTable();
        }

        // 2. Horizontal rule
        if (/^(\-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
          closeLists();
          output.push("<hr>");
          continue;
        }

        // 3. Headings
        if (/^###\s+(.*)/.test(trimmed)) {
          closeLists();
          const title = trimmed.replace(/^###\s+/, "");
          output.push(`<h3>${formatInline(title)}</h3>`);
          continue;
        }
        if (/^##\s+(.*)/.test(trimmed)) {
          closeLists();
          const title = trimmed.replace(/^##\s+/, "");
          output.push(`<h2>${formatInline(title)}</h2>`);
          continue;
        }
        if (/^#\s+(.*)/.test(trimmed)) {
          closeLists();
          const title = trimmed.replace(/^#\s+/, "");
          output.push(`<h1>${formatInline(title)}</h1>`);
          continue;
        }

        // 4. Unordered List (* or -)
        if (/^[\*\-]\s+(.*)/.test(trimmed)) {
          if (!inUl) {
            closeLists();
            inUl = true;
            output.push("<ul>");
          }
          const itemText = trimmed.replace(/^[\*\-]\s+/, "");
          output.push(`<li>${formatInline(itemText)}</li>`);
          continue;
        }

        // 5. Ordered List (1. or 2.)
        if (/^\d+\.\s+(.*)/.test(trimmed)) {
          if (!inOl) {
            closeLists();
            inOl = true;
            output.push("<ol>");
          }
          const itemText = trimmed.replace(/^\d+\.\s+/, "");
          output.push(`<li>${formatInline(itemText)}</li>`);
          continue;
        }

        // 6. Empty line
        if (!trimmed) {
          closeLists();
          continue;
        }

        // 7. Regular paragraph
        closeLists();
        output.push(`<p>${formatInline(trimmed)}</p>`);
      }

      closeLists();
      closeTable();

      return output.join("\n");
    };

    const renderAiHtml = (rawText) => {
      if (!rawText) return "";

      if (typeof window.marked !== "undefined" && typeof window.marked.parse === "function") {
        try {
          const parsed = window.marked.parse(rawText, { gfm: true, breaks: true });
          return parsed
            .replace(/<table>/g, '<div class="table-container"><table>')
            .replace(/<\/table>/g, '</table></div>')
            .replace(/\[(Đang xử lý|Processing)\]/gi, '<span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-500/15 text-amber-600 dark:text-amber-400">Đang xử lý</span>')
            .replace(/\[(Tạm giữ|On-hold)\]/gi, '<span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold bg-purple-500/15 text-purple-600 dark:text-purple-400">Tạm giữ</span>')
            .replace(/\[(Hoàn thành|Completed)\]/gi, '<span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">Hoàn thành</span>')
            .replace(/\[(Đã hủy|Cancelled)\]/gi, '<span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-500/15 text-rose-600 dark:text-rose-400">Đã hủy</span>');
        } catch (e) {
          console.warn("[MiniApp] marked.js error, using fallback:", e);
        }
      }

      return parseMarkdownFallback(rawText);
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
        scrollAiChatToBottom();
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
        scrollAiChatToBottom();
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

    const isRefreshingAll = ref(false);

    const refreshAllData = async (forceServer = true) => {
      if (isRefreshingAll.value) return;
      haptic("medium");
      isRefreshingAll.value = true;
      try {
        if (forceServer) {
          try {
            await apiCall("/api/system/cache-refresh", { method: "POST" });
          } catch (cacheErr) {
            console.warn("Lỗi làm mới cache server:", cacheErr);
          }
        }
        await Promise.allSettled([
          fetchDashboard(),
          fetchProducts(),
          fetchOrders(),
          fetchNotionQueue(),
          currentTab.value === "reviews" ? fetchReviews() : Promise.resolve(),
          currentTab.value === "tools" ? fetchLogs() : Promise.resolve()
        ]);
        showToast("✅ Đã làm mới toàn bộ dữ liệu 2 website (Derma & Pharma)!", "success");
      } catch (err) {
        showToast(err.message || "Lỗi khi làm mới dữ liệu", "error");
      } finally {
        isRefreshingAll.value = false;
      }
    };

    const refreshCurrentTab = () => {
      refreshAllData(true);
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
      selectedStore,
      availableSites,
      setStoreFilter,
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
      aiChatContainer,
      aiMessages,
      aiPrompt,
      isAiThinking,
      clearAiChat,
      isSystemActionRunning,
      systemReportModal,
      formatMoney,
      formatDate,
      getOrderStatusLabel,
      toggleTheme,
      switchTab,
      refreshCurrentTab,
      isRefreshingAll,
      refreshAllData,
      fetchOrders,
      updateOrderStatus,
      fetchProducts,
      onSearchInput,
      goToPage,
      toggleVariationsExpand,
      openEditPriceModal,
      saveProductPrice,
      editImagesModal,
      openEditImagesModal,
      handleImageFileSelected,
      moveImagePosition,
      setFeaturedImage,
      saveImagesOrder,
      deleteProductImage,
      toggleStock,
      deleteProduct,
      fetchReviews,
      approveReview,
      deleteReview,
      fetchNotionQueue,
      triggerNotionSync,
      askGemini,
      sendAiPrompt,
      renderAiHtml,
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
