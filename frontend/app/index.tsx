import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, KeyboardAvoidingView, Linking, Platform, Pressable, SafeAreaView, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as WebBrowser from "expo-web-browser";
import * as SecureStore from "expo-secure-store";
import * as Clipboard from "expo-clipboard";
import * as DocumentPicker from "expo-document-picker";
import Constants from "expo-constants";

WebBrowser.maybeCompleteAuthSession();
const API = `${Constants.expoConfig?.extra?.backendUrl || process.env.EXPO_PUBLIC_BACKEND_URL}/api`;
const APP_ORIGIN = process.env.EXPO_PUBLIC_BACKEND_URL || "";
const colors = { bg: "#17151D", panel: "#221E2B", surface: "#F6F4F8", ink: "#17131D", muted: "#9A93A6", purple: "#9B6CFF", line: "#393244", green: "#55C49A", amber: "#DDAA62" };
type ThreadMsg = { message_id: string; author: string; author_name?: string | null; body: string; created_at: string };
type Attachment = { attachment_id: string; kind: string; name: string; url?: string | null; content_type?: string | null; created_at: string };
type Milestone = { milestone_id: string; title: string; fee: number; expense: number; status: string; payment_status: string; change_request?: string | null; change_status?: string | null; change_thread?: ThreadMsg[]; attachments?: Attachment[]; payment_session_id?: string | null; paid_at?: string | null; payment_reminder_at?: string | null; cleared_by_name?: string | null; cleared_by_email?: string | null; cleared_at?: string | null };
type Engagement = { engagement_id: string; client_name: string; client_email?: string; share_token: string; status: string; scope_accepted_at?: string | null; milestones: Milestone[] };
type Screen = "welcome" | "dashboard" | "create" | "share" | "agency" | "client" | "accept";

const money = (n: number) => `$${n.toLocaleString("en-US", { minimumFractionDigits: 0 })}`;
const pillLabel = (s: string) => s === "active" ? "ACTIVE" : s === "archived" ? "ARCHIVED" : "AWAITING SCOPE";
const pillDotStyle = (s: string) => s === "active" ? undefined : { backgroundColor: s === "archived" ? colors.muted : colors.amber };
const formatDate = (iso?: string | null) => { if (!iso) return ""; try { const d = new Date(iso); return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); } catch { return ""; } };
const getToken = async () => Platform.OS === "web" ? (globalThis as any).localStorage?.getItem("checkpoint_session") : SecureStore.getItemAsync("checkpoint_session");
const saveToken = async (token: string) => Platform.OS === "web" ? (globalThis as any).localStorage?.setItem("checkpoint_session", token) : SecureStore.setItemAsync("checkpoint_session", token);
const clearToken = async () => Platform.OS === "web" ? (globalThis as any).localStorage?.removeItem("checkpoint_session") : SecureStore.deleteItemAsync("checkpoint_session");

function Path({ milestones, onSelect, showClearedBy }: { milestones: Milestone[]; onSelect?: (m: Milestone) => void; showClearedBy?: boolean }) {
  return <View style={styles.path}>{milestones.map((m, i) => <Pressable key={m.milestone_id} onPress={() => onSelect?.(m)} testID={`milestone-row-${i}`} style={({ pressed }) => [styles.pathRow, pressed && styles.pressed]}>
    <View style={styles.trackCol}><View style={[styles.marker, m.status === "cleared" && styles.markerCleared]}>{m.status === "cleared" ? <Ionicons name="checkmark" size={14} color={colors.bg} /> : <Text style={styles.markerText}>{String(i + 1).padStart(2, "0")}</Text>}</View>{i < milestones.length - 1 && <View style={[styles.track, m.status === "cleared" && styles.trackCleared]} />}</View>
    <View style={styles.pathCopy}>
      <Text style={styles.milestoneTitle}>{m.title}</Text>
      <Text style={styles.milestoneMeta}>{money(m.fee)} fee {m.expense ? ` · ${money(m.expense)} expense` : ""}</Text>
      {showClearedBy && m.status === "cleared" && m.cleared_by_name ? <Text style={styles.clearedByLine}>Cleared by {m.cleared_by_name}{m.cleared_at ? `, ${formatDate(m.cleared_at)}` : ""}</Text> : null}
      {m.change_status === "open" && m.change_request ? <Text style={styles.changeRequestLine}>Change requested: {m.change_request}</Text> : null}
      {m.payment_status === "paid" ? <Text style={styles.clearedByLine}>Paid{m.paid_at ? ` ${formatDate(m.paid_at)}` : ""} · Stripe</Text> : null}
    </View>
    <View style={[styles.statusDot, m.status === "cleared" && { backgroundColor: colors.green }]} />
  </Pressable>)}</View>;
}

function Header({ onBack, eyebrow, title, right }: { onBack?: () => void; eyebrow: string; title: string; right?: React.ReactNode }) {
  return <View style={styles.header}>{onBack ? <Pressable onPress={onBack} testID="header-back-button" style={styles.iconButton}><Ionicons name="arrow-back" size={21} color={colors.surface} /></Pressable> : <View style={styles.brandMark}><Ionicons name="navigate" size={17} color={colors.surface} /></View>}<View><Text style={styles.eyebrow}>{eyebrow}</Text><Text style={styles.headerTitle}>{title}</Text></View><View style={{ flex: 1 }} />{right || <Text style={styles.wordmark}>CHECKPOINT</Text>}</View>;
}

export default function Index() {
  const [screen, setScreen] = useState<Screen>("welcome");
  const [engagement, setEngagement] = useState<Engagement | null>(null);
  const [engagements, setEngagements] = useState<Engagement[]>([]);
  const [loading, setLoading] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [clientName, setClientName] = useState("");
  const [clientEmail, setClientEmail] = useState("");
  const [active, setActive] = useState<Milestone | null>(null);
  const [changeNote, setChangeNote] = useState("");
  const [clearName, setClearName] = useState("");
  const [clearEmail, setClearEmail] = useState("");
  const [sessionChecked, setSessionChecked] = useState(false);
  const [threadMsg, setThreadMsg] = useState("");
  const [paying, setPaying] = useState(false);
  const [payBanner, setPayBanner] = useState<"" | "checking" | "paid" | "pending">("");
  const [attName, setAttName] = useState("");
  const [attUrl, setAttUrl] = useState("");
  const [uploading, setUploading] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editFee, setEditFee] = useState("");
  const [editExpense, setEditExpense] = useState("");

  // New engagement draft
  const [draftName, setDraftName] = useState("");
  const [draftEmail, setDraftEmail] = useState("");
  const [draftMilestones, setDraftMilestones] = useState<{ title: string; fee: string; expense: string }[]>([]);
  const [msTitle, setMsTitle] = useState("");
  const [msFee, setMsFee] = useState("");
  const [msExpense, setMsExpense] = useState("");

  const fetchDashboard = async () => {
    const t = await getToken();
    if (!t) return;
    const r = await fetch(`${API}/engagements`, { headers: { Authorization: `Bearer ${t}` } });
    if (r.ok) setEngagements(await r.json());
  };

  const loadSample = async () => {
    setLoading(true);
    try { const r = await fetch(`${API}/public/engagements/checkpoint-demo`); const data = await r.json(); setEngagement(data); setScreen("agency"); }
    catch { Alert.alert("Unable to load workspace", "Please try again."); }
    finally { setLoading(false); }
  };

  const signIn = async () => {
    const redirect = Platform.OS === "web" ? `${window.location.origin}/` : Linking.createURL("");
    const authUrl = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirect)}`;
    if (Platform.OS === "web") { window.location.href = authUrl; return; }
    const result = await WebBrowser.openAuthSessionAsync(authUrl, redirect);
    const url = (result as any).url || await Linking.getInitialURL();
    const match = url?.match(/[?#&]session_id=([^&#]+)/);
    if (match) {
      const r = await fetch(`${API}/auth/session`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: decodeURIComponent(match[1]) }) });
      const data = await r.json();
      if (r.ok) { await saveToken(data.session_token); setAuthed(true); await fetchDashboard(); setScreen("dashboard"); }
    }
  };

  const signOut = async () => { await clearToken(); setAuthed(false); setEngagements([]); setEngagement(null); setScreen("welcome"); };

  useEffect(() => { (async () => {
    // Client share link detection (web: /token, native: deep link)
    const pathToken = Platform.OS === "web"
      ? window.location.pathname.split("/").filter(Boolean).pop()
      : (await Linking.getInitialURL())?.match(/(?:token=|client\/)([^/?#]+)/)?.[1];
    // Web: also handle Google callback that lands here with #session_id=...
    if (Platform.OS === "web") {
      const hash = window.location.hash || "";
      const hashMatch = hash.match(/session_id=([^&#]+)/);
      if (hashMatch) {
        const r = await fetch(`${API}/auth/session`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: decodeURIComponent(hashMatch[1]) }) });
        const data = await r.json();
        if (r.ok) { await saveToken(data.session_token); window.history.replaceState({}, "", "/"); setAuthed(true); const list = await fetch(`${API}/engagements`, { headers: { Authorization: `Bearer ${data.session_token}` } }); if (list.ok) setEngagements(await list.json()); setScreen("dashboard"); setSessionChecked(true); return; }
      }
    }
    if (pathToken && pathToken !== "api" && pathToken !== "index.html") {
      const publicResponse = await fetch(`${API}/public/engagements/${pathToken}`);
      if (publicResponse.ok) {
        setEngagement(await publicResponse.json());
        setScreen("client");
        setSessionChecked(true);
        if (Platform.OS === "web") {
          const sid = new URLSearchParams(window.location.search).get("session_id");
          if (sid) { window.history.replaceState({}, "", `/${pathToken}`); pollPayment(sid, pathToken); }
        }
        return;
      }
    }
    const t = await getToken();
    if (t) {
      const r = await fetch(`${API}/auth/me`, { headers: { Authorization: `Bearer ${t}` } });
      if (r.ok) { setAuthed(true); const list = await fetch(`${API}/engagements`, { headers: { Authorization: `Bearer ${t}` } }); if (list.ok) setEngagements(await list.json()); setScreen("dashboard"); }
    }
    setSessionChecked(true);
  })(); }, []);

  const cleared = useMemo(() => engagement?.milestones.filter(m => m.status === "cleared").length || 0, [engagement]);
  const visible = useMemo(() => showArchived ? engagements : engagements.filter(e => e.status !== "archived"), [engagements, showArchived]);
  const archivedCount = useMemo(() => engagements.filter(e => e.status === "archived").length, [engagements]);
  const totals = useMemo(() => visible.reduce((acc, e) => {
    e.milestones.forEach(m => {
      const amt = m.fee + (m.expense || 0);
      if (m.status === "cleared") acc.cleared += amt;
      if (m.payment_status === "requested") acc.invoiced += amt;
      if (m.payment_status === "paid") acc.paid += amt;
    });
    return acc;
  }, { cleared: 0, invoiced: 0, paid: 0 }), [visible]);

  const accept = async () => {
    if (!engagement) return;
    setLoading(true);
    const r = await fetch(`${API}/public/engagements/${engagement.share_token}/accept`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ client_name: clientName || undefined, client_email: clientEmail || undefined }) });
    if (r.ok) { setEngagement(await r.json()); setScreen("client"); }
    setLoading(false);
  };

  const clear = async (m: Milestone) => {
    if (!engagement) return;
    setLoading(true);
    const r = await fetch(`${API}/public/engagements/${engagement.share_token}/milestones/${m.milestone_id}/clear`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ client_name: clearName || undefined, client_email: clearEmail || undefined }) });
    if (r.ok) { setEngagement(await r.json()); setActive(null); setClearName(""); setClearEmail(""); }
    setLoading(false);
  };

  const requestChange = async (m: Milestone) => {
    if (!engagement || !changeNote.trim()) return;
    setLoading(true);
    const r = await fetch(`${API}/public/engagements/${engagement.share_token}/milestones/${m.milestone_id}/request-change`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note: changeNote.trim(), author_name: clearName.trim() || undefined }) });
    if (r.ok) { syncEngagement(await r.json(), m.milestone_id); setChangeNote(""); }
    setLoading(false);
  };

  const syncEngagement = (data: Engagement, milestoneId: string) => {
    setEngagement(data);
    setActive(data.milestones.find(x => x.milestone_id === milestoneId) || null);
  };

  const pollPayment = async (sessionId: string, token: string) => {
    setPayBanner("checking");
    for (let attempt = 0; attempt < 8; attempt++) {
      try {
        const r = await fetch(`${API}/public/payments/${sessionId}/status`);
        if (r.ok) {
          const d = await r.json();
          if (d.payment_status === "paid") {
            const er = await fetch(`${API}/public/engagements/${token}`);
            if (er.ok) {
              const data: Engagement = await er.json();
              setEngagement(data);
              setActive(prev => prev ? data.milestones.find(x => x.milestone_id === prev.milestone_id) || null : null);
            }
            setPayBanner("paid");
            return;
          }
          if (d.status === "expired") break;
        }
      } catch {}
      await new Promise(res => setTimeout(res, 2000));
    }
    setPayBanner("pending");
  };

  const payMilestone = async (m: Milestone) => {
    if (!engagement) return;
    setPaying(true);
    try {
      const r = await fetch(`${API}/public/engagements/${engagement.share_token}/milestones/${m.milestone_id}/pay`, { method: "POST" });
      const d = await r.json();
      if (r.ok && d.url) {
        if (Platform.OS === "web") { window.location.assign(d.url); return; }
        await WebBrowser.openBrowserAsync(d.url);
        await pollPayment(d.session_id, engagement.share_token);
      } else {
        Alert.alert("Payment unavailable", d.detail || "Please try again.");
      }
    } catch { Alert.alert("Payment unavailable", "Please try again."); }
    setPaying(false);
  };

  const sendThreadMessage = async (m: Milestone) => {
    if (!engagement || !threadMsg.trim()) return;
    setLoading(true);
    let r: Response | null = null;
    if (screen === "client") {
      r = await fetch(`${API}/public/engagements/${engagement.share_token}/milestones/${m.milestone_id}/change-messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body: threadMsg.trim(), author_name: clearName.trim() || undefined }) });
    } else {
      const t = await getToken();
      if (t) r = await fetch(`${API}/engagements/${engagement.engagement_id}/milestones/${m.milestone_id}/change-messages`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${t}` }, body: JSON.stringify({ body: threadMsg.trim() }) });
    }
    if (r?.ok) { syncEngagement(await r.json(), m.milestone_id); setThreadMsg(""); }
    setLoading(false);
  };

  const resolveChange = async (m: Milestone) => {
    if (!engagement) return;
    const t = await getToken();
    if (!t) return;
    setLoading(true);
    const r = await fetch(`${API}/engagements/${engagement.engagement_id}/milestones/${m.milestone_id}/resolve-change`, { method: "POST", headers: { Authorization: `Bearer ${t}` } });
    if (r.ok) syncEngagement(await r.json(), m.milestone_id);
    setLoading(false);
  };

  const addLinkAttachment = async (m: Milestone) => {
    if (!engagement || !attName.trim() || !attUrl.trim()) { Alert.alert("Missing details", "Add a label and a URL."); return; }
    const t = await getToken();
    if (!t) return;
    setLoading(true);
    const url = attUrl.trim().toLowerCase().startsWith("http") ? attUrl.trim() : `https://${attUrl.trim()}`;
    const r = await fetch(`${API}/engagements/${engagement.engagement_id}/milestones/${m.milestone_id}/attachments`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${t}` }, body: JSON.stringify({ name: attName.trim(), url }) });
    if (r.ok) { syncEngagement(await r.json(), m.milestone_id); setAttName(""); setAttUrl(""); }
    else { const d = await r.json().catch(() => ({})); Alert.alert("Could not attach", d.detail || "Please try again."); }
    setLoading(false);
  };

  const uploadAttachment = async (m: Milestone) => {
    if (!engagement) return;
    const t = await getToken();
    if (!t) return;
    const picked = await DocumentPicker.getDocumentAsync({ copyToCacheDirectory: true });
    if (picked.canceled || !picked.assets?.length) return;
    const asset = picked.assets[0];
    setUploading(true);
    try {
      const form = new FormData();
      if (Platform.OS === "web") {
        const blob = await (await fetch(asset.uri)).blob();
        form.append("file", blob, asset.name);
      } else {
        form.append("file", { uri: asset.uri, name: asset.name, type: asset.mimeType || "application/octet-stream" } as any);
      }
      const r = await fetch(`${API}/engagements/${engagement.engagement_id}/milestones/${m.milestone_id}/attachments/upload`, { method: "POST", headers: { Authorization: `Bearer ${t}` }, body: form });
      if (r.ok) syncEngagement(await r.json(), m.milestone_id);
      else { const d = await r.json().catch(() => ({})); Alert.alert("Upload failed", d.detail || "Please try again."); }
    } catch { Alert.alert("Upload failed", "Please try again."); }
    setUploading(false);
  };

  const removeAttachment = async (m: Milestone, attId: string) => {
    if (!engagement) return;
    const t = await getToken();
    if (!t) return;
    const r = await fetch(`${API}/engagements/${engagement.engagement_id}/milestones/${m.milestone_id}/attachments/${attId}`, { method: "DELETE", headers: { Authorization: `Bearer ${t}` } });
    if (r.ok) syncEngagement(await r.json(), m.milestone_id);
  };

  const openAttachment = async (a: Attachment) => {
    if (!engagement) return;
    const url = a.kind === "link" ? (a.url || "") : `${API}/public/engagements/${engagement.share_token}/attachments/${a.attachment_id}`;
    if (!url) return;
    if (Platform.OS === "web") window.open(url, "_blank");
    else await WebBrowser.openBrowserAsync(url);
  };

  const openMilestone = (m: Milestone) => {
    setActive(m);
    setEditTitle(m.title);
    setEditFee(String(m.fee));
    setEditExpense(m.expense ? String(m.expense) : "");
  };

  const saveMilestoneEdit = async (m: Milestone) => {
    if (!engagement) return;
    const t = await getToken();
    if (!t) return;
    const fee = parseFloat(editFee || "0");
    if (!editTitle.trim() || isNaN(fee) || fee <= 0) { Alert.alert("Missing details", "A description and a fee above zero are required."); return; }
    setLoading(true);
    const r = await fetch(`${API}/engagements/${engagement.engagement_id}/milestones/${m.milestone_id}`, { method: "PUT", headers: { "Content-Type": "application/json", Authorization: `Bearer ${t}` }, body: JSON.stringify({ title: editTitle.trim(), fee, expense: parseFloat(editExpense || "0") || 0 }) });
    if (r.ok) { syncEngagement(await r.json(), m.milestone_id); fetchDashboard(); }
    else { const d = await r.json().catch(() => ({})); Alert.alert("Could not save", d.detail || "Please try again."); }
    setLoading(false);
  };

  const moveMilestone = async (m: Milestone, direction: "up" | "down") => {
    if (!engagement) return;
    const t = await getToken();
    if (!t) return;
    const r = await fetch(`${API}/engagements/${engagement.engagement_id}/milestones/${m.milestone_id}/move`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${t}` }, body: JSON.stringify({ direction }) });
    if (r.ok) { syncEngagement(await r.json(), m.milestone_id); fetchDashboard(); }
  };

  const removeMilestone = async (m: Milestone) => {
    if (!engagement) return;
    const t = await getToken();
    if (!t) return;
    const r = await fetch(`${API}/engagements/${engagement.engagement_id}/milestones/${m.milestone_id}`, { method: "DELETE", headers: { Authorization: `Bearer ${t}` } });
    if (r.ok) { setEngagement(await r.json()); setActive(null); fetchDashboard(); }
    else { const d = await r.json().catch(() => ({})); Alert.alert("Could not remove", d.detail || "Please try again."); }
  };

  const addMilestoneToEngagement = async () => {
    if (!engagement) return;
    const fee = parseFloat(msFee || "0");
    if (!msTitle.trim() || isNaN(fee) || fee <= 0) { Alert.alert("Missing details", "Every checkpoint needs a description and a fee."); return; }
    const t = await getToken();
    if (!t) return;
    setLoading(true);
    const r = await fetch(`${API}/engagements/${engagement.engagement_id}/milestones`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${t}` }, body: JSON.stringify({ title: msTitle.trim(), fee, expense: parseFloat(msExpense || "0") || 0 }) });
    if (r.ok) { setEngagement(await r.json()); setMsTitle(""); setMsFee(""); setMsExpense(""); fetchDashboard(); }
    setLoading(false);
  };

  const toggleArchive = async () => {
    if (!engagement) return;
    const t = await getToken();
    if (!t) return;
    setLoading(true);
    const action = engagement.status === "archived" ? "unarchive" : "archive";
    const r = await fetch(`${API}/engagements/${engagement.engagement_id}/${action}`, { method: "POST", headers: { Authorization: `Bearer ${t}` } });
    if (r.ok) { setEngagement(await r.json()); fetchDashboard(); }
    setLoading(false);
  };

  const downloadPdf = async () => {
    if (!engagement) return;
    const url = `${API}/public/engagements/${engagement.share_token}/summary.pdf`;
    if (Platform.OS === "web") window.open(url, "_blank");
    else await WebBrowser.openBrowserAsync(url);
  };

  const addDraftMilestone = () => {
    const fee = parseFloat(msFee || "0");
    if (!msTitle.trim() || isNaN(fee) || fee <= 0) { Alert.alert("Missing details", "Every checkpoint needs a description and a fee."); return; }
    setDraftMilestones(prev => [...prev, { title: msTitle.trim(), fee: String(fee), expense: msExpense || "0" }]);
    setMsTitle(""); setMsFee(""); setMsExpense("");
  };

  const removeDraftMilestone = (idx: number) => setDraftMilestones(prev => prev.filter((_, i) => i !== idx));

  const submitEngagement = async () => {
    if (!draftName.trim() || draftMilestones.length === 0) { Alert.alert("Not ready", "Add a client name and at least one checkpoint."); return; }
    const t = await getToken();
    if (!t) return;
    setLoading(true);
    const body = { client_name: draftName.trim(), client_email: draftEmail.trim() || undefined, milestones: draftMilestones.map(m => ({ title: m.title, fee: parseFloat(m.fee || "0"), expense: parseFloat(m.expense || "0") })) };
    const r = await fetch(`${API}/engagements`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${t}` }, body: JSON.stringify(body) });
    if (r.ok) {
      const created = await r.json();
      setEngagement(created);
      setEngagements(prev => [created, ...prev]);
      setDraftName(""); setDraftEmail(""); setDraftMilestones([]); setMsTitle(""); setMsFee(""); setMsExpense("");
      setScreen("share");
    } else {
      Alert.alert("Could not create engagement", "Please try again.");
    }
    setLoading(false);
  };

  const shareUrl = engagement ? `${APP_ORIGIN}/${engagement.share_token}` : "";
  const copyShareLink = async () => { if (!shareUrl) return; try { await Clipboard.setStringAsync(shareUrl); Alert.alert("Link copied", "Share it with your client."); } catch {} };

  const openEngagement = async (e: Engagement) => {
    setLoading(true);
    const r = await fetch(`${API}/public/engagements/${e.share_token}`);
    if (r.ok) { setEngagement(await r.json()); setScreen("agency"); }
    setLoading(false);
  };

  if (!sessionChecked) return <View style={styles.loading}><ActivityIndicator color={colors.purple} /></View>;

  if (screen === "welcome") return <SafeAreaView style={styles.app}>
    <View style={styles.welcome}>
      <View style={styles.logoLarge}><Ionicons name="navigate" size={27} color={colors.surface} /></View>
      <Text style={styles.display}>Work moves{Platform.OS === "web" ? " online" : " forward"}.</Text>
      <Text style={styles.lede}>Checkpoint keeps scope, approvals, and milestone payments on one clear trajectory.</Text>
      <Pressable onPress={signIn} testID="google-signin-button" style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed]}><Ionicons name="logo-google" size={18} color={colors.surface} /><Text style={styles.primaryText}>Continue with Google</Text></Pressable>
      <Pressable onPress={loadSample} testID="explore-sample-button" style={styles.secondaryButton}><Text style={styles.secondaryText}>Explore sample workspace</Text><Ionicons name="arrow-forward" size={17} color={colors.purple} /></Pressable>
      <Text style={styles.footnote}>For agencies and clients who value a clean record.</Text>
    </View>
  </SafeAreaView>;

  if (screen === "dashboard") return <SafeAreaView style={styles.app}>
    <Header eyebrow="AGENCY CONTROL" title="Engagements" right={<Pressable onPress={signOut} testID="sign-out-button" style={styles.textButton}><Text style={styles.textButtonLabel}>Sign out</Text></Pressable>} />
    <ScrollView contentContainerStyle={styles.content}>
      <View style={styles.dashboardTop}>
        <View><Text style={styles.sectionLabel}>ACTIVE ENGAGEMENTS</Text><Text style={styles.bigStat}>{visible.length} <Text style={styles.bigStatMuted}>total</Text></Text></View>
        <Pressable onPress={() => setScreen("create")} testID="new-engagement-button" style={styles.newButton}><Ionicons name="add" size={18} color={colors.surface} /><Text style={styles.primaryText}>New engagement</Text></Pressable>
      </View>
      {visible.length > 0 && <View style={styles.earningsPanel} testID="earnings-panel">
        <View style={styles.earningsCol}><Text style={styles.earningsLabel}>CLEARED</Text><Text style={styles.earningsValue}>{money(totals.cleared)}</Text></View>
        <View style={styles.earningsDivider} />
        <View style={styles.earningsCol}><Text style={styles.earningsLabel}>AWAITING</Text><Text style={[styles.earningsValue, { color: colors.amber }]}>{money(totals.invoiced)}</Text></View>
        <View style={styles.earningsDivider} />
        <View style={styles.earningsCol}><Text style={styles.earningsLabel}>PAID</Text><Text style={[styles.earningsValue, { color: colors.green }]}>{money(totals.paid)}</Text></View>
      </View>}
      {archivedCount > 0 && <Pressable onPress={() => setShowArchived(v => !v)} testID="toggle-archived-button" style={styles.archiveToggle}><Ionicons name="archive-outline" size={14} color={colors.muted} /><Text style={styles.archiveToggleText}>{showArchived ? "Hide archived" : `Show archived (${archivedCount})`}</Text></Pressable>}
      {visible.length === 0 ? <View style={styles.emptyPanel}>
        <Ionicons name="navigate-outline" size={28} color={colors.muted} />
        <Text style={styles.emptyTitle}>No engagements yet</Text>
        <Text style={styles.emptyBody}>Start a new engagement to define the trajectory and share the plan with your client.</Text>
      </View> : visible.map(e => {
        const clearedCount = e.milestones.filter(m => m.status === "cleared").length;
        const paidCount = e.milestones.filter(m => m.payment_status === "paid").length;
        const requestedCount = e.milestones.filter(m => m.payment_status === "requested").length;
        const openChanges = e.milestones.filter(m => m.change_status === "open").length;
        return <Pressable key={e.engagement_id} onPress={() => openEngagement(e)} testID={`engagement-card-${e.engagement_id}`} style={({ pressed }) => [styles.engagementCard, pressed && styles.pressed]}>
          <View style={styles.rowBetween}>
            <View style={{ flex: 1 }}>
              <Text style={styles.engagementName}>{e.client_name}</Text>
              <Text style={styles.engagementMeta}>{clearedCount} of {e.milestones.length} milestones cleared</Text>
            </View>
            <View style={styles.statusPill}><View style={[styles.pillDot, pillDotStyle(e.status)]} /><Text style={styles.pillText}>{pillLabel(e.status)}</Text></View>
          </View>
          <View style={styles.progressBar}><View style={[styles.progressFill, { width: `${(clearedCount / e.milestones.length) * 100}%` }]} /></View>
          <View style={styles.rowBetween}>
            <Text style={styles.engagementMeta}>{paidCount} paid · {requestedCount} awaiting payment{openChanges > 0 ? ` · ${openChanges} open change${openChanges > 1 ? "s" : ""}` : ""}</Text>
            <Text style={styles.engagementLink}>/{e.share_token.slice(0, 12)}…</Text>
          </View>
        </Pressable>;
      })}
    </ScrollView>
  </SafeAreaView>;

  if (screen === "create") return <SafeAreaView style={styles.app}>
    <Header onBack={() => setScreen("dashboard")} eyebrow="NEW ENGAGEMENT" title="Define trajectory" />
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <View style={styles.lightCard}>
          <Text style={styles.cardKicker}>STEP 1 · CLIENT</Text>
          <Text style={styles.cardTitle}>Who is this engagement with?</Text>
          <TextInput placeholder="Client name" placeholderTextColor="#9A93A6" value={draftName} onChangeText={setDraftName} testID="draft-client-name-input" style={styles.input} />
          <TextInput placeholder="Client email (optional)" placeholderTextColor="#9A93A6" value={draftEmail} onChangeText={setDraftEmail} keyboardType="email-address" autoCapitalize="none" testID="draft-client-email-input" style={styles.input} />
        </View>
        <View style={styles.lightCard}>
          <Text style={styles.cardKicker}>STEP 2 · CHECKPOINTS</Text>
          <Text style={styles.cardTitle}>Add each milestone.</Text>
          <Text style={styles.bodyDark}>List them in the order they should be cleared. Each generates a payment request when the client clears it.</Text>
          {draftMilestones.length > 0 && <View style={styles.draftList}>{draftMilestones.map((m, i) => <View key={i} style={styles.draftRow}>
            <View style={styles.draftIndex}><Text style={styles.draftIndexText}>{String(i + 1).padStart(2, "0")}</Text></View>
            <View style={{ flex: 1 }}>
              <Text style={styles.draftTitle}>{m.title}</Text>
              <Text style={styles.draftMeta}>{money(parseFloat(m.fee))} fee{parseFloat(m.expense) > 0 ? ` · ${money(parseFloat(m.expense))} expense` : ""}</Text>
            </View>
            <Pressable onPress={() => removeDraftMilestone(i)} testID={`remove-draft-${i}`} style={styles.close}><Ionicons name="close" size={18} color={colors.ink} /></Pressable>
          </View>)}</View>}
          <TextInput placeholder="Checkpoint description" placeholderTextColor="#9A93A6" value={msTitle} onChangeText={setMsTitle} testID="milestone-title-input" style={styles.input} />
          <View style={styles.rowGap}>
            <TextInput placeholder="Fee (USD)" placeholderTextColor="#9A93A6" value={msFee} onChangeText={setMsFee} keyboardType="decimal-pad" testID="milestone-fee-input" style={[styles.input, { flex: 1 }]} />
            <TextInput placeholder="Expense (optional)" placeholderTextColor="#9A93A6" value={msExpense} onChangeText={setMsExpense} keyboardType="decimal-pad" testID="milestone-expense-input" style={[styles.input, { flex: 1 }]} />
          </View>
          <Pressable onPress={addDraftMilestone} testID="add-milestone-button" style={styles.outlineButton}><Ionicons name="add" size={17} color={colors.ink} /><Text style={styles.changeText}>Add checkpoint</Text></Pressable>
        </View>
        <View style={styles.lightCard}>
          <Text style={styles.cardKicker}>STEP 3 · READY</Text>
          <Text style={styles.cardTitle}>Create engagement</Text>
          <Text style={styles.bodyDark}>{`A shareable link will be generated. Send it to your client — they'll accept scope before work begins.`}</Text>
          <Pressable onPress={submitEngagement} disabled={loading} testID="submit-engagement-button" style={styles.darkButton}>{loading ? <ActivityIndicator color={colors.surface} /> : <><Text style={styles.primaryText}>Generate share link</Text><Ionicons name="arrow-forward" size={19} color={colors.surface} /></>}</Pressable>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  </SafeAreaView>;

  if (screen === "share" && engagement) return <SafeAreaView style={styles.app}>
    <Header onBack={() => setScreen("dashboard")} eyebrow="READY TO SHARE" title={engagement.client_name} />
    <ScrollView contentContainerStyle={styles.content}>
      <View style={styles.lightCard}>
        <Text style={styles.cardKicker}>ENGAGEMENT CREATED</Text>
        <Text style={styles.cardTitle}>Share this link with your client.</Text>
        <Text style={styles.bodyDark}>{`They'll review the trajectory and accept the full plan before any checkpoint moves.`}</Text>
        {engagement.client_email ? <View style={styles.emailNote}><Ionicons name="mail-outline" size={15} color="#4C8A70" /><Text style={styles.emailNoteText}>We emailed {engagement.client_email} this link automatically.</Text></View> : null}
        <View style={styles.linkBox}>
          <Ionicons name="link-outline" size={18} color={colors.purple} />
          <Text style={styles.linkText} numberOfLines={1} testID="share-link-text">{shareUrl}</Text>
        </View>
        <Pressable onPress={copyShareLink} testID="copy-link-button" style={styles.darkButton}><Ionicons name="copy-outline" size={18} color={colors.surface} /><Text style={styles.primaryText}>Copy link</Text></Pressable>
        <Pressable onPress={() => setScreen("agency")} testID="view-engagement-button" style={styles.outlineButton}><Text style={styles.changeText}>Open engagement</Text><Ionicons name="arrow-forward" size={17} color={colors.ink} /></Pressable>
      </View>
    </ScrollView>
  </SafeAreaView>;

  if (!engagement) return null;

  if (screen === "accept") return <SafeAreaView style={styles.app}>
    <Header onBack={() => setScreen("client")} eyebrow="REVIEW REQUIRED" title="Accept scope" />
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <View style={styles.lightCard}>
          <Text style={styles.cardKicker}>BEFORE WORK BEGINS</Text>
          <Text style={styles.cardTitle}>Confirm the full plan.</Text>
          <Text style={styles.bodyDark}>Review the trajectory below. Your timestamped acceptance is the shared record that unlocks the first checkpoint.</Text>
          <Path milestones={engagement.milestones} />
          <TextInput placeholder="Your name (optional)" placeholderTextColor="#9A93A6" value={clientName} onChangeText={setClientName} testID="accept-name-input" style={styles.input} />
          <TextInput placeholder="Email (optional)" placeholderTextColor="#9A93A6" value={clientEmail} onChangeText={setClientEmail} keyboardType="email-address" autoCapitalize="none" testID="accept-email-input" style={styles.input} />
          <Pressable onPress={accept} testID="accept-scope-button" style={styles.darkButton}>{loading ? <ActivityIndicator color={colors.surface} /> : <><Text style={styles.primaryText}>Accept scope & schedule</Text><Ionicons name="checkmark-circle-outline" size={19} color={colors.surface} /></>}</Pressable>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  </SafeAreaView>;

  const isClient = screen === "client";
  const backTo = isClient ? undefined : (authed ? () => setScreen("dashboard") : () => setScreen("welcome"));
  const canEdit = !isClient && authed && engagement.status === "awaiting_scope_acceptance";

  return <SafeAreaView style={styles.app}>
    <Header onBack={backTo} eyebrow={isClient ? "CLIENT PORTAL" : "AGENCY CONTROL"} title={engagement.client_name} />
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
    <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <View style={styles.topline}>
        <View><Text style={styles.sectionLabel}>{isClient ? "ENGAGEMENT STATUS" : "ACTIVE ENGAGEMENT"}</Text><Text style={styles.bigStat}>{cleared} <Text style={styles.bigStatMuted}>/ {engagement.milestones.length}</Text></Text><Text style={styles.statCaption}>checkpoints cleared</Text></View>
        <View style={styles.statusPill}><View style={[styles.pillDot, pillDotStyle(engagement.status)]} /><Text style={styles.pillText}>{pillLabel(engagement.status)}</Text></View>
      </View>
      {payBanner !== "" && <View style={[styles.payBanner, payBanner === "paid" && styles.payBannerPaid]}>
        {payBanner === "checking" ? <ActivityIndicator size="small" color={colors.purple} /> : <Ionicons name={payBanner === "paid" ? "checkmark-circle" : "time-outline"} size={18} color={payBanner === "paid" ? colors.green : colors.amber} />}
        <Text style={styles.payBannerText} testID="payment-banner-text">{payBanner === "checking" ? "Confirming your payment with Stripe…" : payBanner === "paid" ? "Payment received — thank you!" : "Payment not confirmed yet — it can take a moment. Check back shortly."}</Text>
      </View>}
      {isClient && engagement.status === "awaiting_scope_acceptance" && <Pressable onPress={() => setScreen("accept")} testID="open-accept-button" style={styles.notice}><Ionicons name="lock-open-outline" size={19} color={colors.purple} /><View style={{ flex: 1 }}><Text style={styles.noticeTitle}>Scope acceptance required</Text><Text style={styles.noticeBody}>Accept the plan before the first milestone can move.</Text></View><Ionicons name="chevron-forward" size={18} color={colors.purple} /></Pressable>}
      {isClient && engagement.status === "archived" && <View style={styles.notice}><Ionicons name="archive-outline" size={19} color={colors.muted} /><View style={{ flex: 1 }}><Text style={styles.noticeTitle}>Engagement archived</Text><Text style={styles.noticeBody}>This engagement has been closed by the agency. The record below is read-only.</Text></View></View>}
      {!isClient && <View style={styles.linkBoxDark}><Ionicons name="link-outline" size={16} color={colors.purple} /><Text style={styles.linkTextDark} numberOfLines={1} testID="agency-share-link">{shareUrl}</Text><Pressable onPress={copyShareLink} testID="agency-copy-link" style={styles.smallCopy}><Ionicons name="copy-outline" size={15} color={colors.purple} /></Pressable></View>}
      {!isClient && <View style={styles.toolRow}>
        <Pressable onPress={downloadPdf} testID="download-pdf-button" style={styles.toolButton}><Ionicons name="document-text-outline" size={15} color={colors.purple} /><Text style={styles.toolButtonText}>PDF summary</Text></Pressable>
        {authed && <Pressable onPress={toggleArchive} disabled={loading} testID="archive-button" style={styles.toolButton}><Ionicons name={engagement.status === "archived" ? "arrow-undo-outline" : "archive-outline"} size={15} color={colors.purple} /><Text style={styles.toolButtonText}>{engagement.status === "archived" ? "Restore" : "Archive"}</Text></Pressable>}
      </View>}
      <View style={styles.sectionHead}><Text style={styles.sectionTitle}>Trajectory</Text><Text style={styles.sectionHint}>{isClient ? "Tap a checkpoint to review" : "Shared with client"}</Text></View>
      <View style={styles.pathCard}><Path milestones={engagement.milestones} onSelect={openMilestone} showClearedBy /></View>
      {canEdit && <View style={styles.lightCard}>
        <Text style={styles.cardKicker}>ADD CHECKPOINT</Text>
        <TextInput placeholder="Checkpoint description" placeholderTextColor="#9A93A6" value={msTitle} onChangeText={setMsTitle} testID="agency-add-title-input" style={styles.input} />
        <View style={styles.rowGap}>
          <TextInput placeholder="Fee (USD)" placeholderTextColor="#9A93A6" value={msFee} onChangeText={setMsFee} keyboardType="decimal-pad" testID="agency-add-fee-input" style={[styles.input, { flex: 1 }]} />
          <TextInput placeholder="Expense (optional)" placeholderTextColor="#9A93A6" value={msExpense} onChangeText={setMsExpense} keyboardType="decimal-pad" testID="agency-add-expense-input" style={[styles.input, { flex: 1 }]} />
        </View>
        <Pressable onPress={addMilestoneToEngagement} disabled={loading} testID="agency-add-milestone-button" style={styles.outlineButton}><Ionicons name="add" size={17} color={colors.ink} /><Text style={styles.changeText}>Add checkpoint</Text></Pressable>
      </View>}
      {active && <View style={styles.lightCard}>
        <View style={styles.rowBetween}>
          <View><Text style={styles.cardKicker}>CHECKPOINT {engagement.milestones.indexOf(active) + 1}</Text><Text style={styles.cardTitle}>{active.title}</Text></View>
          <Pressable onPress={() => { setActive(null); setChangeNote(""); setClearName(""); setClearEmail(""); setThreadMsg(""); }} testID="close-milestone-button" style={styles.close}><Ionicons name="close" size={19} color={colors.ink} /></Pressable>
        </View>
        <Text style={styles.bodyDark}>{active.status === "cleared" ? (active.payment_status === "paid" ? "This checkpoint is cleared and the milestone payment has been received." : "This checkpoint is cleared. A payment request has been generated for the milestone amount.") : "Review the deliverable, then clear it or request changes before work proceeds."}</Text>
        {active.status === "cleared" && active.cleared_by_name ? <Text style={styles.clearedByLineDark}>Cleared by {active.cleared_by_name}{active.cleared_at ? `, ${formatDate(active.cleared_at)}` : ""}</Text> : null}
        <View style={styles.paymentLine}><Text style={styles.paymentLabel}>Milestone fee</Text><Text style={styles.paymentAmount}>{money(active.fee)}</Text></View>
        {active.expense > 0 && <View style={styles.paymentLineInner}><Text style={styles.paymentLabel}>Expense</Text><Text style={styles.paymentAmountSm}>{money(active.expense)}</Text></View>}
        {canEdit && <View style={styles.attachBox}>
          <Text style={[styles.subKicker, { marginTop: 0 }]}>EDIT CHECKPOINT</Text>
          <TextInput placeholder="Checkpoint description" placeholderTextColor="#9A93A6" value={editTitle} onChangeText={setEditTitle} testID="edit-title-input" style={styles.input} />
          <View style={styles.rowGap}>
            <TextInput placeholder="Fee (USD)" placeholderTextColor="#9A93A6" value={editFee} onChangeText={setEditFee} keyboardType="decimal-pad" testID="edit-fee-input" style={[styles.input, { flex: 1 }]} />
            <TextInput placeholder="Expense" placeholderTextColor="#9A93A6" value={editExpense} onChangeText={setEditExpense} keyboardType="decimal-pad" testID="edit-expense-input" style={[styles.input, { flex: 1 }]} />
          </View>
          <View style={[styles.rowGap, { marginTop: 14 }]}>
            <Pressable onPress={() => saveMilestoneEdit(active)} disabled={loading} testID="save-milestone-button" style={[styles.changeButton, { flex: 1 }]}>{loading ? <ActivityIndicator color={colors.ink} /> : <Text style={styles.changeText}>Save changes</Text>}</Pressable>
            <Pressable onPress={() => moveMilestone(active, "up")} testID="move-up-button" style={styles.iconAction}><Ionicons name="arrow-up" size={17} color={colors.ink} /></Pressable>
            <Pressable onPress={() => moveMilestone(active, "down")} testID="move-down-button" style={styles.iconAction}><Ionicons name="arrow-down" size={17} color={colors.ink} /></Pressable>
            <Pressable onPress={() => removeMilestone(active)} testID="remove-milestone-button" style={styles.iconAction}><Ionicons name="trash-outline" size={17} color="#B0503C" /></Pressable>
          </View>
        </View>}
        {(((active.attachments?.length || 0) > 0) || !isClient) && <View style={styles.attachBox}>
          <Text style={[styles.subKicker, { marginTop: 0 }]}>DELIVERABLES</Text>
          {(active.attachments || []).map(a => <View key={a.attachment_id} style={styles.attachRow}>
            <Ionicons name={a.kind === "link" ? "link-outline" : "document-attach-outline"} size={16} color={colors.purple} />
            <Pressable onPress={() => openAttachment(a)} style={{ flex: 1, minHeight: 30, justifyContent: "center" }} testID={`open-attachment-${a.attachment_id}`}><Text style={styles.attachName} numberOfLines={1}>{a.name}</Text></Pressable>
            {!isClient && <Pressable onPress={() => removeAttachment(active, a.attachment_id)} testID={`remove-attachment-${a.attachment_id}`} style={styles.close}><Ionicons name="trash-outline" size={16} color="#8A8194" /></Pressable>}
          </View>)}
          {(active.attachments?.length || 0) === 0 && <Text style={styles.attachEmpty}>No deliverables attached yet — add a preview link or upload a file for your client.</Text>}
          {!isClient && <>
            <TextInput placeholder="Label (e.g. Final cut v2)" placeholderTextColor="#9A93A6" value={attName} onChangeText={setAttName} testID="attachment-name-input" style={styles.input} />
            <TextInput placeholder="https://preview-link.com" placeholderTextColor="#9A93A6" value={attUrl} onChangeText={setAttUrl} autoCapitalize="none" keyboardType="url" testID="attachment-url-input" style={styles.input} />
            <View style={[styles.rowGap, { marginTop: 14 }]}>
              <Pressable onPress={() => addLinkAttachment(active)} disabled={loading} testID="attach-link-button" style={[styles.changeButton, { flex: 1 }]}><Text style={styles.changeText}>Attach link</Text></Pressable>
              <Pressable onPress={() => uploadAttachment(active)} disabled={uploading} testID="upload-file-button" style={[styles.changeButton, { flex: 1 }]}>{uploading ? <ActivityIndicator color={colors.ink} /> : <Text style={styles.changeText}>Upload file</Text>}</Pressable>
            </View>
          </>}
        </View>}
        {active.status !== "cleared" && isClient && engagement.status === "active" && <>
          <Text style={styles.subKicker}>YOUR NAME & EMAIL (FOR THE RECORD)</Text>
          <TextInput placeholder="Your name" placeholderTextColor="#9A93A6" value={clearName} onChangeText={setClearName} testID="clear-name-input" style={styles.input} />
          <TextInput placeholder="Email (optional)" placeholderTextColor="#9A93A6" value={clearEmail} onChangeText={setClearEmail} keyboardType="email-address" autoCapitalize="none" testID="clear-email-input" style={styles.input} />
          <TextInput placeholder="What needs to change?" placeholderTextColor="#9A93A6" value={changeNote} onChangeText={setChangeNote} testID="change-note-input" style={styles.input} />
          <View style={styles.actionRow}>
            <Pressable onPress={() => clear(active)} testID="clear-milestone-button" style={styles.darkButton}>{loading ? <ActivityIndicator color={colors.surface} /> : <Text style={styles.primaryText}>Clear checkpoint</Text>}</Pressable>
            <Pressable onPress={() => requestChange(active)} testID="request-change-button" style={styles.changeButton}><Text style={styles.changeText}>Request changes</Text></Pressable>
          </View>
        </>}
        {active.status === "cleared" && <View style={styles.paymentRequest}>
          <Ionicons name={active.payment_status === "paid" ? "checkmark-circle" : "card-outline"} size={18} color={active.payment_status === "paid" ? colors.green : colors.purple} />
          <View style={{ flex: 1 }}>
            <Text style={[styles.paymentRequestText, active.payment_status === "paid" && { color: colors.green }]} testID="payment-status-text">{active.payment_status === "paid" ? `Paid · ${money(active.fee + (active.expense || 0))}` : `Payment request · ${money(active.fee + (active.expense || 0))}`}</Text>
            <Text style={styles.paymentRequestSub}>{active.payment_status === "paid" ? `Received via Stripe${active.paid_at ? ` · ${formatDate(active.paid_at)}` : ""}.` : isClient ? "Pay securely in one tap with Stripe — card or wallet." : "Stripe payment link is live — your client can pay from their portal."}</Text>
            {active.payment_status !== "paid" && isClient && <Pressable onPress={() => payMilestone(active)} disabled={paying} testID="pay-milestone-button" style={styles.payButton}>{paying ? <ActivityIndicator color={colors.surface} /> : <><Ionicons name="lock-closed" size={15} color={colors.surface} /><Text style={styles.primaryText}>Pay {money(active.fee + (active.expense || 0))}</Text></>}</Pressable>}
          </View>
        </View>}
        {((active.change_thread?.length || 0) > 0) && <View style={styles.threadBox}>
          <View style={styles.rowBetween}>
            <Text style={[styles.subKicker, { marginTop: 0 }]}>CHANGE REQUEST THREAD</Text>
            <View style={[styles.threadPill, active.change_status === "resolved" && styles.threadPillResolved]}><Text style={[styles.threadPillText, active.change_status === "resolved" && { color: colors.green }]} testID="thread-status-pill">{active.change_status === "resolved" ? "RESOLVED" : "OPEN"}</Text></View>
          </View>
          {(active.change_thread || []).map(msg => <View key={msg.message_id} style={[styles.threadMsg, msg.author === "agency" && styles.threadMsgAgency]}>
            <Text style={styles.threadAuthor}>{msg.author === "agency" ? `${msg.author_name || "Agency"} · AGENCY` : msg.author_name || "Client"} · {formatDate(msg.created_at)}</Text>
            <Text style={styles.threadBody}>{msg.body}</Text>
          </View>)}
          {active.change_status !== "resolved" && <>
            <TextInput placeholder={isClient ? "Reply to the agency…" : "Reply to your client…"} placeholderTextColor="#9A93A6" value={threadMsg} onChangeText={setThreadMsg} testID="thread-message-input" style={styles.input} />
            <View style={[styles.actionRow, { marginTop: 4 }]}>
              <Pressable onPress={() => sendThreadMessage(active)} disabled={loading} testID="thread-send-button" style={styles.darkButton}>{loading ? <ActivityIndicator color={colors.surface} /> : <Text style={styles.primaryText}>Send reply</Text>}</Pressable>
              {!isClient && <Pressable onPress={() => resolveChange(active)} testID="resolve-change-button" style={styles.changeButton}><Text style={styles.changeText}>Mark resolved</Text></Pressable>}
            </View>
          </>}
        </View>}
      </View>}
    </ScrollView>
    </KeyboardAvoidingView>
  </SafeAreaView>;
}

const styles = StyleSheet.create({
  app: { flex: 1, backgroundColor: colors.bg },
  loading: { flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center" },
  welcome: { flex: 1, padding: 28, justifyContent: "center", maxWidth: 560, alignSelf: "center", width: "100%" },
  logoLarge: { width: 54, height: 54, borderRadius: 16, backgroundColor: colors.purple, alignItems: "center", justifyContent: "center", marginBottom: 30 },
  display: { color: colors.surface, fontSize: 40, lineHeight: 45, fontWeight: "800", letterSpacing: -1.5, maxWidth: 390 },
  lede: { color: colors.muted, fontSize: 17, lineHeight: 26, marginTop: 18, maxWidth: 420, marginBottom: 38 },
  primaryButton: { backgroundColor: colors.purple, minHeight: 54, borderRadius: 8, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 10 },
  primaryText: { color: colors.surface, fontWeight: "700", fontSize: 15 },
  secondaryButton: { minHeight: 54, borderRadius: 8, borderWidth: 1, borderColor: colors.line, marginTop: 12, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 12 },
  secondaryText: { color: colors.surface, fontSize: 15, fontWeight: "600" },
  footnote: { color: colors.muted, textAlign: "center", fontSize: 12, marginTop: 35 },
  header: { minHeight: 82, paddingHorizontal: 22, flexDirection: "row", alignItems: "center", gap: 12, borderBottomWidth: 1, borderBottomColor: colors.line },
  brandMark: { width: 34, height: 34, borderRadius: 10, backgroundColor: colors.purple, alignItems: "center", justifyContent: "center" },
  iconButton: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  eyebrow: { color: colors.purple, fontSize: 10, fontWeight: "800", letterSpacing: 1.5 },
  headerTitle: { color: colors.surface, fontSize: 20, fontWeight: "700", marginTop: 2 },
  wordmark: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 1.5 },
  textButton: { paddingHorizontal: 10, paddingVertical: 6 },
  textButtonLabel: { color: colors.muted, fontSize: 12, fontWeight: "700", letterSpacing: 0.5 },
  content: { padding: 22, paddingBottom: 60, maxWidth: 720, width: "100%", alignSelf: "center" },
  topline: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", paddingVertical: 22 },
  dashboardTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-end", paddingTop: 12, paddingBottom: 22 },
  newButton: { backgroundColor: colors.purple, paddingHorizontal: 18, minHeight: 46, borderRadius: 8, flexDirection: "row", alignItems: "center", gap: 8 },
  sectionLabel: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 1.3 },
  bigStat: { color: colors.surface, fontSize: 48, fontWeight: "800", marginTop: 3 },
  bigStatMuted: { color: colors.muted, fontSize: 24 },
  statCaption: { color: colors.muted, fontSize: 13, marginTop: -4 },
  statusPill: { flexDirection: "row", gap: 7, alignItems: "center", borderWidth: 1, borderColor: colors.line, paddingHorizontal: 10, paddingVertical: 8, borderRadius: 20 },
  pillDot: { width: 7, height: 7, borderRadius: 7, backgroundColor: colors.green },
  pillText: { color: colors.surface, fontSize: 10, fontWeight: "800", letterSpacing: 1 },
  sectionHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "baseline", marginTop: 16, marginBottom: 12 },
  sectionTitle: { color: colors.surface, fontWeight: "700", fontSize: 19 },
  sectionHint: { color: colors.muted, fontSize: 12 },
  pathCard: { backgroundColor: colors.panel, borderRadius: 12, padding: 18 },
  path: { paddingVertical: 2 },
  pathRow: { minHeight: 64, flexDirection: "row", alignItems: "flex-start" },
  trackCol: { width: 30, alignItems: "center" },
  marker: { width: 28, height: 28, borderRadius: 14, borderWidth: 1, borderColor: colors.muted, alignItems: "center", justifyContent: "center", backgroundColor: colors.panel },
  markerCleared: { borderColor: colors.green, backgroundColor: colors.green },
  markerText: { color: colors.muted, fontSize: 10, fontWeight: "800" },
  track: { width: 1, flex: 1, minHeight: 36, backgroundColor: colors.line },
  trackCleared: { backgroundColor: colors.green },
  pathCopy: { flex: 1, paddingLeft: 14, paddingTop: 1 },
  milestoneTitle: { color: colors.surface, fontSize: 15, fontWeight: "600" },
  milestoneMeta: { color: colors.muted, fontSize: 12, marginTop: 4 },
  clearedByLine: { color: colors.green, fontSize: 11, marginTop: 4, fontWeight: "600" },
  changeRequestLine: { color: colors.amber, fontSize: 11, marginTop: 4, fontWeight: "600" },
  statusDot: { width: 7, height: 7, borderRadius: 7, backgroundColor: colors.line, marginTop: 10 },
  notice: { backgroundColor: "#2A2338", borderWidth: 1, borderColor: "#60469A", borderRadius: 10, padding: 15, flexDirection: "row", alignItems: "center", gap: 12, marginBottom: 12 },
  noticeTitle: { color: colors.surface, fontWeight: "700", fontSize: 14 },
  noticeBody: { color: "#C2B8D5", fontSize: 12, marginTop: 3 },
  linkBoxDark: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.panel, borderRadius: 8, paddingHorizontal: 14, paddingVertical: 12, marginBottom: 6 },
  linkTextDark: { color: colors.surface, fontSize: 12, flex: 1 },
  smallCopy: { width: 30, height: 30, alignItems: "center", justifyContent: "center" },
  lightCard: { backgroundColor: colors.surface, borderRadius: 12, padding: 22, marginTop: 18 },
  cardKicker: { color: colors.purple, fontSize: 10, fontWeight: "800", letterSpacing: 1.3 },
  cardTitle: { color: colors.ink, fontSize: 23, fontWeight: "800", marginTop: 7 },
  bodyDark: { color: "#625B6B", fontSize: 14, lineHeight: 21, marginTop: 10 },
  subKicker: { color: colors.purple, fontSize: 9, fontWeight: "800", letterSpacing: 1.3, marginTop: 18 },
  input: { borderBottomWidth: 1, borderBottomColor: "#D7D1DD", paddingVertical: 14, color: colors.ink, fontSize: 15, marginTop: 8 },
  rowGap: { flexDirection: "row", gap: 14 },
  outlineButton: { minHeight: 46, borderWidth: 1, borderColor: "#D0C6DA", borderRadius: 8, alignItems: "center", justifyContent: "center", marginTop: 16, flexDirection: "row", gap: 8 },
  darkButton: { backgroundColor: colors.ink, minHeight: 52, borderRadius: 8, marginTop: 18, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 10 },
  actionRow: { gap: 10 },
  changeButton: { minHeight: 46, borderWidth: 1, borderColor: "#D0C6DA", borderRadius: 8, alignItems: "center", justifyContent: "center" },
  changeText: { color: colors.ink, fontWeight: "700", fontSize: 14 },
  rowBetween: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  close: { width: 36, height: 36, alignItems: "center", justifyContent: "center" },
  paymentLine: { borderTopWidth: 1, borderBottomWidth: 1, borderColor: "#DDD7E1", marginTop: 20, paddingVertical: 14, flexDirection: "row", justifyContent: "space-between" },
  paymentLineInner: { paddingVertical: 8, flexDirection: "row", justifyContent: "space-between" },
  paymentLabel: { color: "#625B6B", fontSize: 13 },
  paymentAmount: { color: colors.ink, fontWeight: "800", fontSize: 16 },
  paymentAmountSm: { color: colors.ink, fontWeight: "700", fontSize: 14 },
  paymentRequest: { flexDirection: "row", alignItems: "flex-start", gap: 10, marginTop: 16, borderTopWidth: 1, borderTopColor: "#DDD7E1", paddingTop: 16 },
  paymentRequestText: { color: colors.purple, fontSize: 13, fontWeight: "700" },
  paymentRequestSub: { color: "#8A8194", fontSize: 11, marginTop: 3, lineHeight: 15 },
  clearedByLineDark: { color: colors.green, fontSize: 12, marginTop: 8, fontWeight: "600" },
  payButton: { backgroundColor: colors.purple, minHeight: 46, borderRadius: 8, marginTop: 14, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingHorizontal: 18, alignSelf: "flex-start" },
  payBanner: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.panel, borderRadius: 10, padding: 14, marginBottom: 12, borderWidth: 1, borderColor: colors.line },
  payBannerPaid: { borderColor: colors.green },
  payBannerText: { color: colors.surface, fontSize: 13, flex: 1, fontWeight: "600" },
  threadBox: { marginTop: 18, borderTopWidth: 1, borderTopColor: "#DDD7E1", paddingTop: 16 },
  threadPill: { borderWidth: 1, borderColor: colors.amber, borderRadius: 12, paddingHorizontal: 8, paddingVertical: 3 },
  threadPillResolved: { borderColor: colors.green },
  threadPillText: { color: colors.amber, fontSize: 9, fontWeight: "800", letterSpacing: 1 },
  threadMsg: { backgroundColor: "#EEEBF1", borderRadius: 10, padding: 12, marginTop: 10 },
  threadMsgAgency: { backgroundColor: "#E9E1F7" },
  threadAuthor: { color: "#8A8194", fontSize: 10, fontWeight: "700", letterSpacing: 0.4 },
  threadBody: { color: colors.ink, fontSize: 13, marginTop: 4, lineHeight: 19 },
  emailNote: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 12 },
  emailNoteText: { color: "#4C8A70", fontSize: 12, fontWeight: "600", flex: 1 },
  earningsPanel: { flexDirection: "row", backgroundColor: colors.panel, borderRadius: 12, paddingVertical: 18, paddingHorizontal: 8, marginBottom: 6, alignItems: "center" },
  earningsCol: { flex: 1, alignItems: "center", gap: 5 },
  earningsLabel: { color: colors.muted, fontSize: 9, fontWeight: "800", letterSpacing: 1.2 },
  earningsValue: { color: colors.surface, fontSize: 19, fontWeight: "800" },
  earningsDivider: { width: 1, height: 34, backgroundColor: colors.line },
  attachBox: { marginTop: 18, borderTopWidth: 1, borderTopColor: "#DDD7E1", paddingTop: 16 },
  attachRow: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: "#EEEBF1", borderRadius: 8, paddingHorizontal: 12, paddingVertical: 8, marginTop: 10 },
  attachName: { color: colors.purple, fontSize: 13, fontWeight: "700" },
  attachEmpty: { color: "#8A8194", fontSize: 12, marginTop: 10, lineHeight: 17 },
  toolRow: { flexDirection: "row", gap: 10, marginBottom: 8 },
  toolButton: { flexDirection: "row", alignItems: "center", gap: 7, borderWidth: 1, borderColor: colors.line, borderRadius: 8, paddingHorizontal: 14, minHeight: 40, justifyContent: "center" },
  toolButtonText: { color: colors.surface, fontSize: 12, fontWeight: "700" },
  archiveToggle: { flexDirection: "row", alignItems: "center", gap: 7, alignSelf: "flex-start", paddingVertical: 10, paddingHorizontal: 4, marginTop: 2 },
  archiveToggleText: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  iconAction: { width: 46, minHeight: 46, borderWidth: 1, borderColor: "#D0C6DA", borderRadius: 8, alignItems: "center", justifyContent: "center" },
  emptyPanel: { backgroundColor: colors.panel, borderRadius: 12, padding: 32, alignItems: "center", marginTop: 12 },
  emptyTitle: { color: colors.surface, fontSize: 17, fontWeight: "700", marginTop: 14 },
  emptyBody: { color: colors.muted, fontSize: 13, textAlign: "center", marginTop: 8, lineHeight: 19, maxWidth: 320 },
  engagementCard: { backgroundColor: colors.panel, borderRadius: 12, padding: 18, marginTop: 12, gap: 12 },
  engagementName: { color: colors.surface, fontSize: 17, fontWeight: "700" },
  engagementMeta: { color: colors.muted, fontSize: 12, marginTop: 4 },
  engagementLink: { color: colors.purple, fontSize: 11, fontWeight: "600" },
  progressBar: { height: 4, backgroundColor: colors.line, borderRadius: 2, overflow: "hidden" },
  progressFill: { height: 4, backgroundColor: colors.purple },
  draftList: { marginTop: 14, gap: 10 },
  draftRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: "#EEEBF1", borderRadius: 8, padding: 12 },
  draftIndex: { width: 26, height: 26, borderRadius: 13, backgroundColor: colors.ink, alignItems: "center", justifyContent: "center" },
  draftIndexText: { color: colors.surface, fontSize: 10, fontWeight: "800" },
  draftTitle: { color: colors.ink, fontSize: 14, fontWeight: "700" },
  draftMeta: { color: "#625B6B", fontSize: 11, marginTop: 2 },
  linkBox: { flexDirection: "row", alignItems: "center", gap: 10, borderWidth: 1, borderColor: "#D0C6DA", borderRadius: 8, paddingHorizontal: 14, paddingVertical: 14, marginTop: 16 },
  linkText: { color: colors.ink, fontSize: 13, flex: 1, fontWeight: "600" },
  pressed: { opacity: 0.72 },
});
