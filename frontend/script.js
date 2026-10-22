console.log("JS is working");

const API = ["127.0.0.1", "localhost"].includes(location.hostname)
  ? "http://127.0.0.1:8000"
  : "https://brewhaven-api-rjkm.onrender.com";
const GOOGLE_CLIENT_ID = "776335343723-6ctb6270u31ii0rp8vr8j3qv2g0fqdcj.apps.googleusercontent.com";

const modal = document.getElementById("auth-modal");
const authBox = document.getElementById("auth-container");
const signinBtn = document.getElementById("signin-btn");
const msg = document.getElementById("auth-msg");

const forgotModal = document.getElementById("forgot-modal");
const forgotMsg = document.getElementById("forgot-msg");

let afterLogin = null; // what to do right after a successful login

// ---------- Open / close the login modal ----------
signinBtn.addEventListener("click", (e) => {
  e.preventDefault();
  afterLogin = null;
  if (localStorage.getItem("token")) return logout();
  authBox.classList.remove("active");
  msg.textContent = "";
  modal.classList.add("open");
});

document.getElementById("modal-close").onclick = () => modal.classList.remove("open");
modal.addEventListener("click", (e) => {
  if (e.target === modal) modal.classList.remove("open");
});

// ---------- Slide between Login and Register ----------
document.getElementById("show-register").onclick = () => {
  authBox.classList.add("active");
  msg.textContent = "";
};
document.getElementById("show-login").onclick = () => {
  authBox.classList.remove("active");
  msg.textContent = "";
};

// ---------- Save the session after any successful login ----------

function saveSession(body) {
  localStorage.setItem("token", body.access_token);
  localStorage.setItem("name", body.name);
  modal.classList.remove("open");
  updateAuthUI();
  showToast(`Welcome, ${body.name.split(" ")[0]}!`);

  checkAdmin().then((isAdmin) => {
    if (isAdmin && !afterLogin) {
      location.href = "admin.html"; // admins land on the dashboard
      return;
    }
    if (afterLogin) {
      const next = afterLogin;
      afterLogin = null;
      next();
    }
  });
}

function showToast(text) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = text;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}
// ---------- Email + password login / register ----------
async function submitAuth(e, path) {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  try {
    const res = await fetch(`${API}/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "Something went wrong");
    saveSession(body);
  } catch (err) {
    msg.textContent = err.message;
  }
}
document.getElementById("login-form").onsubmit = (e) => submitAuth(e, "login");
document.getElementById("register-form").onsubmit = (e) => submitAuth(e, "register");

// ---------- Forgot password ----------
document.getElementById("forgot-link").addEventListener("click", (e) => {
  e.preventDefault();
  modal.classList.remove("open");
  forgotMsg.textContent = "";
  forgotModal.classList.add("open");
});
document.getElementById("forgot-close").onclick = () => forgotModal.classList.remove("open");
forgotModal.addEventListener("click", (e) => {
  if (e.target === forgotModal) forgotModal.classList.remove("open");
});

document.getElementById("forgot-form").onsubmit = async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  try {
    const res = await fetch(`${API}/forgot-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "Something went wrong");
    forgotMsg.style.color = "green";
    forgotMsg.textContent = body.message;
  } catch (err) {
    forgotMsg.style.color = "tomato";
    forgotMsg.textContent = err.message;
  }
};

// ---------- Google login ----------
async function onGoogleCredential(response) {
  try {
    const res = await fetch(`${API}/auth/google`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential: response.credential }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "Google sign-in failed");
    saveSession(body);
  } catch (err) {
    msg.textContent = err.message;
  }
}

function initGoogle() {
  if (!window.google) return; // Google script blocked or offline
  google.accounts.id.initialize({
    client_id: GOOGLE_CLIENT_ID,
    callback: onGoogleCredential,
  });
  ["google-login-btn", "google-register-btn"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      google.accounts.id.renderButton(el, {
        type: "icon",
        shape: "square",
        theme: "outline",
        size: "large",
      });
    }
  });
}
window.addEventListener("load", initGoogle);

// ---------- Logout and button text ----------
function logout() {
  localStorage.removeItem("token");
  localStorage.removeItem("name");
  localStorage.removeItem("admin");
  updateAuthUI();
}

function updateAuthUI() {
  const name = localStorage.getItem("name");
  const loggedIn = !!localStorage.getItem("token");
  signinBtn.textContent = name ? `Hi, ${name.split(" ")[0]} (Logout)` : "Sign in";

  const adminLink = document.getElementById("admin-link");
  if (adminLink) adminLink.hidden = !localStorage.getItem("admin");

  const guestFields = document.getElementById("guest-fields");
  if (guestFields) guestFields.hidden = loggedIn;
  const signinCheckout = document.getElementById("signin-checkout");
  if (signinCheckout) signinCheckout.hidden = loggedIn;
}
updateAuthUI();
if (localStorage.getItem("token")) checkAdmin();

async function checkAdmin() {
  try {
    const res = await authFetch("/me");
    const me = await res.json();
    if (me.is_admin) localStorage.setItem("admin", "1");
    else localStorage.removeItem("admin");
    updateAuthUI();
    return !!me.is_admin;
  } catch {
    return false;
  }
}

// ---------- Contact form ----------
const contactMsg = document.getElementById("contact-msg");
document.getElementById("contact-form").onsubmit = async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  try {
    const res = await fetch(`${API}/contact`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "Something went wrong");
    contactMsg.style.color = "lightgreen";
    contactMsg.textContent = body.message;
    e.target.reset();
  } catch (err) {
    contactMsg.style.color = "tomato";
    contactMsg.textContent = err.message;
  }
};

// ---------- Highlight the nav link for the section you're viewing ----------
const navLinks = document.querySelectorAll("nav a[href^='#']");
const sections = [...navLinks]
  .map((a) => document.querySelector(a.getAttribute("href")))
  .filter(Boolean);

const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        navLinks.forEach((a) =>
          a.classList.toggle("active", a.getAttribute("href") === `#${entry.target.id}`)
        );
      }
    });
  },
  { rootMargin: "-40% 0px -55% 0px" }
);
sections.forEach((s) => observer.observe(s));
// ================= Products + Cart =================
const money = (cents) =>
  `KSh ${(cents / 100).toLocaleString("en-KE", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let products = [];
let cart = JSON.parse(localStorage.getItem("cart") || "{}"); // { productId: quantity }

const grid = document.getElementById("product-grid");
const drawer = document.getElementById("cart-drawer");
const overlay = document.getElementById("cart-overlay");
const cartItemsEl = document.getElementById("cart-items");

function stars(rating) {
  let html = "";
  for (let i = 1; i <= 5; i++) {
    if (rating >= i) html += '<i class="bi bi-star-fill"></i>';
    else if (rating >= i - 0.5) html += '<i class="bi bi-star-half"></i>';
    else html += '<i class="bi bi-star"></i>';
  }
  return html;
}

function productCard(p) {
  return `
    <div class="product-card">
      <div class="product-img"><img src="assets/images/${esc(p.image)}" alt="${esc(p.name)}"></div>
      <div class="product-info">
        ${p.tag ? `<div class="product-tag">${esc(p.tag)}</div>` : ""}
        <h3 class="product-title">${esc(p.name)}</h3>
        <p class="product-desc">${esc(p.description)}</p>
        <div class="product-footer">
          <div class="product-price">${money(p.price_cents)}</div>
          <div class="rating">${stars(p.rating)}</div>
        </div>
        <button class="add-cart" data-id="${p.id}">Add to cart</button>
      </div>
    </div>`;
}

async function loadProducts() {
  try {
    const res = await fetch(`${API}/products`);
    if (!res.ok) throw new Error(`The server answered ${res.status}`);
    products = await res.json();
    grid.innerHTML = products.length
      ? products.map(productCard).join("")
      : '<p class="section-desc">No products are available right now.</p>';
    // drop cart items for products that no longer exist
    Object.keys(cart).forEach((id) => {
      if (!products.some((p) => String(p.id) === id)) delete cart[id];
    });
    renderCart();
  } catch (err) {
    console.error("Could not load products:", err);
    grid.innerHTML = '<p class="section-desc">Our menu is unavailable right now. Please try again shortly.</p>';
  }
}

function saveCart() {
  localStorage.setItem("cart", JSON.stringify(cart));
  renderCart();
}

function renderCart() {
  const entries = Object.entries(cart)
    .map(([id, qty]) => ({ p: products.find((x) => String(x.id) === id), qty }))
    .filter((e) => e.p);

  const count = entries.reduce((sum, e) => sum + e.qty, 0);
  const total = entries.reduce((sum, e) => sum + e.p.price_cents * e.qty, 0);

  const badge = document.getElementById("cart-count");
  badge.textContent = count;
  badge.dataset.zero = count === 0 ? "1" : "0";
  document.getElementById("cart-total").textContent = money(total);

  cartItemsEl.innerHTML = entries.length
    ? entries
        .map(
          ({ p, qty }) => `
      <li class="cart-item">
        <img src="assets/images/${esc(p.image)}" alt="${esc(p.name)}">
        <div class="ci-info"><strong>${esc(p.name)}</strong><span>${money(p.price_cents * qty)}</span></div>
        <div class="qty">
          <button data-act="dec" data-id="${p.id}">&minus;</button>
          <span>${qty}</span>
          <button data-act="inc" data-id="${p.id}">+</button>
        </div>
        <button class="ci-remove" data-act="remove" data-id="${p.id}" aria-label="Remove">&times;</button>
      </li>`
        )
        .join("")
    : '<li class="cart-empty">Your cart is empty.</li>';
}

function openCart() { drawer.classList.add("open"); overlay.classList.add("open"); }
function closeCart() { drawer.classList.remove("open"); overlay.classList.remove("open"); }

// Add to cart buttons
grid.addEventListener("click", (e) => {
  const btn = e.target.closest(".add-cart");
  if (!btn) return;
  const id = btn.dataset.id;
  cart[id] = (cart[id] || 0) + 1;
  saveCart();
  showToast("Added to cart");
});

// + / - / remove inside the drawer
cartItemsEl.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id;
  if (btn.dataset.act === "inc") cart[id] += 1;
  if (btn.dataset.act === "dec") cart[id] -= 1;
  if (btn.dataset.act === "remove" || cart[id] <= 0) delete cart[id];
  saveCart();
});

// Open / close
document.getElementById("cart-btn").onclick = openCart;
document.getElementById("order-btn").addEventListener("click", (e) => {
  e.preventDefault();
  openCart();
});
document.getElementById("cart-close").onclick = closeCart;
overlay.onclick = closeCart;

// ---------- Authenticated requests ----------
async function authFetch(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${localStorage.getItem("token")}`,
      ...(options.headers || {}),
    },
  });
  if (res.status === 401) {
    logout();
    throw new Error("Your session expired. Please log in again.");
  }
  return res;
}

function openLogin() {
  document.getElementById("auth-container").classList.remove("active");
  modal.classList.add("open");
}

// ---------- Checkout ----------
const checkoutBtn = document.getElementById("checkout-btn");
const nameInput = document.getElementById("order-name");
const emailInput = document.getElementById("order-email");
const phoneInput = document.getElementById("order-phone");

// Remember a returning customer's details on this device
const savedCustomer = JSON.parse(localStorage.getItem("customer") || "{}");
nameInput.value = savedCustomer.name || "";
emailInput.value = savedCustomer.email || "";
phoneInput.value = savedCustomer.phone || "";

function rememberGuestOrder(order) {
  const list = JSON.parse(localStorage.getItem("guestOrders") || "[]");
  list.push({ id: order.id, token: order.track_token });
  localStorage.setItem("guestOrders", JSON.stringify(list.slice(-10)));
}

async function placeOrder() {
  if (!Object.keys(cart).length) return showToast("Your cart is empty");

  const loggedIn = !!localStorage.getItem("token");
  const method = mpesaEnabled ? selectedPayment() : "cash";
  const payload = {
    items: Object.entries(cart).map(([id, qty]) => ({ product_id: Number(id), quantity: qty })),
    note: document.getElementById("order-note").value,
    phone: phoneInput.value.trim(),
    payment_method: method,
  };

  if (!loggedIn) {
    payload.name = nameInput.value.trim();
    payload.email = emailInput.value.trim();
    if (payload.name.length < 2) {
      openCart();
      return showToast("Please enter your name");
    }
    if (!payload.phone) {
      openCart();
      return showToast("Please enter your phone number");
    }
  }
  if (method === "mpesa" && !payload.phone) {
    openCart();
    return showToast("Enter the phone number to pay with M-Pesa");
  }

  checkoutBtn.disabled = true;
  checkoutBtn.textContent = "Placing order...";
  try {
    const res = loggedIn
      ? await authFetch("/orders", { method: "POST", body: JSON.stringify(payload) })
      : await fetch(`${API}/orders`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "Could not place your order");

    if (!loggedIn) rememberGuestOrder(body);
    localStorage.setItem(
      "customer",
      JSON.stringify({
        name: nameInput.value.trim(),
        email: emailInput.value.trim(),
        phone: phoneInput.value.trim(),
      })
    );

    cart = {};
    saveCart();
    document.getElementById("order-note").value = "";
    closeCart();

    if (method === "mpesa") {
      watchPayment(body);
    } else {
      const emailed = loggedIn || payload.email ? " We've emailed you a confirmation." : "";
      showToast(`Order #${body.id} placed! Total ${money(body.total_cents)}.${emailed}`);
    }
  } catch (err) {
    openCart(); // reopen the cart so the customer can fix whatever went wrong
    showToast(err.message);
  } finally {
    checkoutBtn.disabled = false;
    checkoutBtn.textContent = "Place order";
  }
}

checkoutBtn.onclick = placeOrder;

// ---------- M-Pesa payment ----------
let mpesaEnabled = false;
let currentPayment = null; // { id, token } of the order being paid
let payTimer = null;

const payModal = document.getElementById("pay-modal");

function selectedPayment() {
  const el = document.querySelector('input[name="pay"]:checked');
  return el ? el.value : "cash";
}

function updatePayNote() {
  document.getElementById("pay-note").textContent =
    mpesaEnabled && selectedPayment() === "mpesa"
      ? "You'll get an M-Pesa prompt on your phone."
      : "Pay when you pick up.";
}

async function loadConfig() {
  try {
    const res = await fetch(`${API}/config`);
    const cfg = await res.json();
    mpesaEnabled = !!cfg.mpesa_enabled;
  } catch {
    mpesaEnabled = false;
  }
  document.getElementById("pay-options").hidden = !mpesaEnabled;
  updatePayNote();
}
document.querySelectorAll('input[name="pay"]').forEach((r) => r.addEventListener("change", updatePayNote));

function showPayModal(title, text, retry = false) {
  document.getElementById("pay-title").textContent = title;
  document.getElementById("pay-text").textContent = text;
  document.getElementById("pay-retry").hidden = !retry;
  payModal.classList.add("open");
}

function stopPolling() {
  clearInterval(payTimer);
  payTimer = null;
}

function watchPayment(order) {
  currentPayment = { id: order.id, token: order.track_token };
  stopPolling();

  if (order.payment_status === "failed") {
    return showPayModal(
      "Payment didn't start",
      `${order.payment_error || "We couldn't send the M-Pesa request."} Your order #${order.id} is saved.`,
      true
    );
  }

  showPayModal("Check your phone", `We sent an M-Pesa request for order #${order.id}. Enter your PIN to pay.`);

  let tries = 0;
  payTimer = setInterval(async () => {
    tries += 1;
    try {
      const res = await fetch(`${API}/orders/track/${order.id}?token=${encodeURIComponent(order.track_token)}`);
      const data = await res.json();
      if (data.payment_status === "paid") {
        stopPolling();
        const code = data.mpesa_receipt ? ` M-Pesa code: ${data.mpesa_receipt}.` : "";
        showPayModal("Payment received", `Thank you! Order #${order.id} is paid.${code}`);
      } else if (data.payment_status === "failed") {
        stopPolling();
        showPayModal(
          "Payment not completed",
          "The M-Pesa payment was cancelled or didn't go through. Try again, or pay when you pick up.",
          true
        );
      }
    } catch {
      /* network hiccup: keep trying */
    }
    if (tries >= 30) {
      stopPolling();
      showPayModal(
        "Still waiting",
        "We haven't received the payment yet. If you already paid, it will show in My orders shortly. Otherwise try again, or pay when you pick up.",
        true
      );
    }
  }, 4000);
}

document.getElementById("pay-retry").onclick = async () => {
  if (!currentPayment) return;
  showPayModal("Check your phone", "Sending a new M-Pesa request...");
  try {
    const res = await fetch(`${API}/orders/${currentPayment.id}/pay`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: currentPayment.token, phone: phoneInput.value.trim() }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "Could not send the payment request");
    watchPayment({ id: currentPayment.id, track_token: currentPayment.token, payment_status: "pending" });
  } catch (err) {
    showPayModal("Payment didn't start", err.message, true);
  }
};

document.getElementById("pay-close").onclick = () => {
  stopPolling();
  payModal.classList.remove("open");
};

loadConfig();

// Sign in first, then the order is placed automatically
document.getElementById("signin-checkout").onclick = () => {
  closeCart();
  afterLogin = Object.keys(cart).length ? placeOrder : null;
  openLogin();
};

// ---------- My orders ----------
const ordersModal = document.getElementById("orders-modal");
const ordersList = document.getElementById("orders-list");

function paymentLabel(o) {
  if (o.payment_method !== "mpesa") return "Pay at pickup";
  return (
    { paid: "Paid with M-Pesa", pending: "Awaiting M-Pesa payment", failed: "M-Pesa payment failed" }[
      o.payment_status
    ] || "M-Pesa"
  );
}

function orderCard(o) {
  const items = o.items.map((i) => `<li>${i.quantity} x ${esc(i.name)}</li>`).join("");
  return `
    <div class="order-card">
      <div class="order-top">
        <strong>Order #${o.id}</strong>
        <span class="status status-${esc(o.status)}">${esc(o.status)}</span>
      </div>
      <small>${new Date(o.created_at).toLocaleString()}</small>
      <ul>${items}</ul>
      ${o.note ? `<small>Note: ${esc(o.note)}</small><br>` : ""}
      <small>${paymentLabel(o)}${o.mpesa_receipt ? ` (${esc(o.mpesa_receipt)})` : ""}</small>
      <div class="order-total">${money(o.total_cents)}</div>
    </div>`;
}

async function loadGuestOrders() {
  const saved = JSON.parse(localStorage.getItem("guestOrders") || "[]");
  const results = await Promise.all(
    saved.map(async ({ id, token }) => {
      try {
        const res = await fetch(`${API}/orders/track/${id}?token=${encodeURIComponent(token)}`);
        return res.ok ? await res.json() : null;
      } catch {
        return null;
      }
    })
  );
  return results.filter(Boolean).reverse();
}

async function openOrders() {
  closeCart();
  ordersList.innerHTML = "<p>Loading...</p>";
  ordersModal.classList.add("open");
  const loggedIn = !!localStorage.getItem("token");
  try {
    let orders;
    if (loggedIn) {
      const res = await authFetch("/orders/mine");
      orders = await res.json();
      if (!res.ok) throw new Error("Could not load your orders.");
    } else {
      orders = await loadGuestOrders();
    }
    let html = orders.length
      ? orders.map(orderCard).join("")
      : "<p>You haven't placed any orders yet.</p>";
    if (!loggedIn) {
      html +=
        '<p class="guest-hint">Guest orders are saved on this device only. Create an account to see your future orders on any device.</p>';
    }
    ordersList.innerHTML = html;
  } catch (err) {
    ordersList.innerHTML = `<p>${esc(err.message)}</p>`;
  }
}

document.getElementById("my-orders-btn").onclick = openOrders;
document.getElementById("orders-close").onclick = () => ordersModal.classList.remove("open");
ordersModal.addEventListener("click", (e) => {
  if (e.target === ordersModal) ordersModal.classList.remove("open");
});

loadProducts();
renderCart();