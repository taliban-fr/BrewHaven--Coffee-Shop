const API = "http://127.0.0.1:8000";
const token = localStorage.getItem("token");
const STATUSES = ["pending", "preparing", "ready", "completed", "cancelled"];

const $ = (id) => document.getElementById(id);
const money = (cents) =>
  `KSh ${(cents / 100).toLocaleString("en-KE", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const when = (iso) => {
  const d = new Date(iso);
  return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};

function toast(text) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = text;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  });
  if (res.status === 401) {
    localStorage.removeItem("token");
    localStorage.removeItem("name");
    localStorage.removeItem("admin");
    location.replace("index.html");
    throw new Error("Please log in again");
  }
  if (res.status === 403) {
    location.replace("index.html");
    throw new Error("Admins only");
  }
  const body = await res.json();
  if (!res.ok) throw new Error(body.detail || "Something went wrong");
  return body;
}

// ---------- Views ----------
const loaders = {
  overview: loadOverview,
  orders: loadOrders,
  products: loadProducts,
  messages: loadMessages,
  users: loadUsers,
};

function show(view) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  document
    .querySelectorAll(".side button[data-view]")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  loaders[view]().catch((err) => toast(err.message));
}

document.querySelectorAll(".side button[data-view]").forEach((b) => {
  b.onclick = () => show(b.dataset.view);
});

$("logout").onclick = () => {
  localStorage.removeItem("token");
  localStorage.removeItem("name");
  localStorage.removeItem("admin");
  location.replace("index.html");
};

// ---------- Overview ----------
async function loadOverview() {
  const s = await api("/admin/stats");
  const cards = [
    ["Total orders", s.orders],
    ["Revenue", money(s.revenue_cents)],
    ["Pending orders", s.pending],
    ["Customers", s.users],
    ["Messages", s.messages],
  ];
  $("stats").innerHTML = cards
    .map(([label, value]) => `<div class="stat"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
}

// ---------- Orders ----------
let allOrders = [];

async function loadOrders() {
  allOrders = await api("/admin/orders");
  renderOrders();
}

function renderOrders() {
  const filter = $("order-filter").value;
  const list = allOrders.filter((o) => !filter || o.status === filter);
  $("orders-body").innerHTML = list.length
    ? list
        .map(
          (o) => `
    <tr>
      <td>#${o.id}</td>
      <td>${esc(o.customer || "-")}${o.guest ? " <small>(guest)</small>" : ""}<br><small>${esc(o.email || "")}</small><br><small>${esc(o.phone || "")}</small></td>
      <td>${o.items.map((i) => `${i.quantity} x ${esc(i.name)}`).join("<br>")}${
        o.note ? `<br><small>Note: ${esc(o.note)}</small>` : ""
      }</td>
      <td>${money(o.total_cents)}<br><small>${
        o.payment_method === "mpesa" ? "M-Pesa: " + esc(o.payment_status) : "Cash at pickup"
      }${o.mpesa_receipt ? " (" + esc(o.mpesa_receipt) + ")" : ""}</small></td>
      <td>${when(o.created_at)}</td>
      <td>
        <select class="status-select status-${o.status}" data-id="${o.id}">
          ${STATUSES.map((s) => `<option value="${s}" ${s === o.status ? "selected" : ""}>${s}</option>`).join("")}
        </select>
      </td>
    </tr>`
        )
        .join("")
    : '<tr><td colspan="6">No orders found.</td></tr>';
}

$("order-filter").onchange = renderOrders;

$("orders-body").addEventListener("change", async (e) => {
  const sel = e.target.closest(".status-select");
  if (!sel) return;
  const order = allOrders.find((o) => String(o.id) === sel.dataset.id);
  try {
    await api(`/admin/orders/${order.id}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status: sel.value }),
    });
    order.status = sel.value;
    renderOrders();
    toast(`Order #${order.id} marked ${sel.value}`);
  } catch (err) {
    sel.value = order.status;
    toast(err.message);
  }
});

// ---------- Products ----------
let allProducts = [];

async function loadProducts() {
  allProducts = await api("/admin/products");
  renderProducts();
}

function renderProducts() {
  $("products-body").innerHTML = allProducts
    .map(
      (p) => `
    <tr>
      <td><img class="thumb" src="assets/images/${esc(p.image)}" alt=""></td>
      <td><strong>${esc(p.name)}</strong>${p.tag ? ` <small>(${esc(p.tag)})</small>` : ""}</td>
      <td>${money(p.price_cents)}</td>
      <td>${p.active ? "Visible" : "Hidden"}</td>
      <td>
        <button class="btn small" data-edit="${p.id}">Edit</button>
        <button class="btn small ghost" data-toggle="${p.id}">${p.active ? "Hide" : "Show"}</button>
      </td>
    </tr>`
    )
    .join("");
}

function payloadFrom(p) {
  return {
    name: p.name,
    description: p.description,
    price_cents: p.price_cents,
    image: p.image,
    tag: p.tag,
    rating: p.rating,
    active: p.active,
  };
}

function resetForm() {
  $("product-form").reset();
  $("pf-id").value = "";
  $("pf-active").checked = true;
  $("pf-rating").value = 5;
}

$("pf-clear").onclick = resetForm;

$("products-body").addEventListener("click", async (e) => {
  const editBtn = e.target.closest("[data-edit]");
  const toggleBtn = e.target.closest("[data-toggle]");

  if (editBtn) {
    const p = allProducts.find((x) => String(x.id) === editBtn.dataset.edit);
    $("pf-id").value = p.id;
    $("pf-name").value = p.name;
    $("pf-price").value = (p.price_cents / 100).toFixed(2);
    $("pf-image").value = p.image;
    $("pf-tag").value = p.tag || "";
    $("pf-rating").value = p.rating;
    $("pf-active").checked = p.active;
    $("pf-desc").value = p.description;
    $("product-form").scrollIntoView({ behavior: "smooth" });
  }

  if (toggleBtn) {
    const p = allProducts.find((x) => String(x.id) === toggleBtn.dataset.toggle);
    try {
      await api(`/admin/products/${p.id}`, {
        method: "PUT",
        body: JSON.stringify({ ...payloadFrom(p), active: !p.active }),
      });
      toast(p.active ? "Product hidden from the shop" : "Product is visible again");
      await loadProducts();
    } catch (err) {
      toast(err.message);
    }
  }
});

$("product-form").onsubmit = async (e) => {
  e.preventDefault();
  const price = Math.round(parseFloat($("pf-price").value) * 100);
  if (!price || price < 1) return toast("Enter a valid price");
  const id = $("pf-id").value;
  const payload = {
    name: $("pf-name").value,
    description: $("pf-desc").value,
    price_cents: price,
    image: $("pf-image").value,
    tag: $("pf-tag").value.trim() || null,
    rating: Number($("pf-rating").value),
    active: $("pf-active").checked,
  };
  try {
    await api(id ? `/admin/products/${id}` : "/admin/products", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    toast(id ? "Product updated" : "Product added");
    resetForm();
    await loadProducts();
  } catch (err) {
    toast(err.message);
  }
};

// ---------- Messages ----------
async function loadMessages() {
  const msgs = await api("/admin/messages");
  $("messages-list").innerHTML = msgs.length
    ? msgs
        .map(
          (m) => `
    <div class="msg-card">
      <strong>${esc(m.name)}</strong> <small>${esc(m.email)} - ${when(m.created_at)}</small>
      <p>${esc(m.message)}</p>
    </div>`
        )
        .join("")
    : "<p>No messages yet.</p>";
}

// ---------- Users ----------
async function loadUsers() {
  const users = await api("/admin/users");
  $("users-body").innerHTML = users
    .map(
      (u) => `
    <tr><td>${u.id}</td><td>${esc(u.name)}</td><td>${esc(u.email)}</td><td>${when(u.created_at)}</td></tr>`
    )
    .join("");
}

// ---------- Start: only admins get in ----------
async function init() {
  try {
    const me = await api("/me");
    if (!me.is_admin) return location.replace("index.html");
  } catch {
    return;
  }
  show("overview");
}

if (token) init();
else location.replace("index.html");