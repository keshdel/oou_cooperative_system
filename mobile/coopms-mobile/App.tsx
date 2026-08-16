import { Ionicons } from '@expo/vector-icons';
import * as Notifications from 'expo-notifications';
import React, { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View
} from 'react-native';

import {
  applyForLoan,
  clearToken,
  getDashboard,
  getLoanDetail,
  getNotifications,
  getProfile,
  getSavings,
  loadToken,
  login,
  markAllNotificationsRead,
  previewLoanSchedule,
  registerDevice,
  updateProfile,
  withdrawLoan
} from './src/api';
import { API_BASE_URL } from './src/config';
import type { DashboardPayload, Loan, MobileNotification, SavingRow } from './src/types';

type Tab = 'home' | 'profile' | 'savings' | 'loans' | 'notifications';

const NAVY = '#0B3475';
const BLUE = '#1554B7';
const YELLOW = '#F8B91E';
const BG = '#F3F6FA';
const INK = '#172033';

function money(value?: number) {
  return `NGN ${(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

async function tryRegisterPushToken(token: string) {
  try {
    const permission = await Notifications.requestPermissionsAsync();
    if (!permission.granted) return;
    const push = await Notifications.getExpoPushTokenAsync();
    await registerDevice(token, push.data, Platform.OS, `${Platform.OS} device`);
  } catch {
    // Push setup should never block login or dashboard access.
  }
}

function LoginScreen({ onLogin }: { onLogin: (token: string) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!username.trim() || !password) {
      Alert.alert('Missing details', 'Enter your CoopMS username/email and password.');
      return;
    }
    setBusy(true);
    try {
      const response = await login(username.trim(), password);
      onLogin(response.token);
      void tryRegisterPushToken(response.token);
    } catch (error) {
      Alert.alert('Login failed', error instanceof Error ? error.message : 'Please try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <SafeAreaView style={styles.authRoot}>
      <StatusBar barStyle="light-content" />
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.authContent}>
        <View style={styles.logoMark}>
          <Ionicons name="people-circle" size={42} color={NAVY} />
        </View>
        <Text style={styles.brand}>COOPMS</Text>
        <Text style={styles.tagline}>Digital Cooperative Management Platform</Text>
        <View style={styles.loginCard}>
          <Text style={styles.loginTitle}>Member Sign In</Text>
          <TextInput
            value={username}
            onChangeText={setUsername}
            autoCapitalize="none"
            keyboardType="email-address"
            placeholder="Email or username"
            style={styles.input}
          />
          <TextInput
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            placeholder="Password"
            style={styles.input}
          />
          <Pressable style={[styles.primaryButton, busy && styles.disabled]} onPress={submit} disabled={busy}>
            {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryButtonText}>Sign In</Text>}
          </Pressable>
          <Text style={styles.apiHint}>Connected to {API_BASE_URL.replace('https://', '')}</Text>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Header({ title, onLogout }: { title: string; onLogout: () => void }) {
  return (
    <View style={styles.header}>
      <View>
        <Text style={styles.headerEyebrow}>COOPMS</Text>
        <Text style={styles.headerTitle}>{title}</Text>
      </View>
      <Pressable onPress={onLogout} style={styles.iconButton}>
        <Ionicons name="log-out-outline" size={20} color={NAVY} />
      </Pressable>
    </View>
  );
}

function StatCard({ label, value, tone }: { label: string; value: string; tone?: 'yellow' | 'green' }) {
  return (
    <View style={[styles.statCard, tone === 'yellow' && styles.statYellow, tone === 'green' && styles.statGreen]}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={styles.statValue}>{value}</Text>
    </View>
  );
}

function HomeScreen({ data, reload }: { data: DashboardPayload; reload: () => void }) {
  const completion = data.member.profile_completion;
  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <View style={styles.memberBand}>
        <View>
          <Text style={styles.memberName}>{data.member.full_name}</Text>
          <Text style={styles.memberMeta}>{data.member.member_number} - {data.member.status}</Text>
        </View>
        {completion.certified_member ? (
          <View style={styles.certifiedBadge}>
            <Ionicons name="shield-checkmark" size={16} color={NAVY} />
            <Text style={styles.certifiedText}>Certified</Text>
          </View>
        ) : null}
      </View>

      <View style={styles.statGrid}>
        <StatCard label="Savings" value={money(data.member.total_savings)} />
        <StatCard label="Share Capital" value={money(data.member.share_capital)} tone="yellow" />
        <StatCard label="Loan Balance" value={money(data.summary.active_loan_balance)} />
        <StatCard label="Profile" value={`${completion.percent}%`} tone={completion.percent === 100 ? 'green' : 'yellow'} />
      </View>

      <View style={styles.card}>
        <View style={styles.rowBetween}>
          <Text style={styles.cardTitle}>Recent Notifications</Text>
          <Pressable onPress={reload}>
            <Ionicons name="refresh" size={18} color={BLUE} />
          </Pressable>
        </View>
        {data.notifications.slice(0, 3).map((item) => (
          <View key={item.id} style={styles.listRow}>
            <Text style={styles.rowTitle}>{item.title}</Text>
            <Text style={styles.rowSub}>{item.message}</Text>
          </View>
        ))}
        {data.notifications.length === 0 ? <Text style={styles.emptyText}>No notifications yet.</Text> : null}
      </View>
    </ScrollView>
  );
}

function ProfileScreen({ token, data, reload }: { token: string; data: DashboardPayload; reload: () => void }) {
  const [profile, setProfile] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getProfile(token).then((response) => setProfile(response.profile)).catch(() => undefined);
  }, [token]);

  async function save() {
    setBusy(true);
    try {
      await updateProfile(token, profile);
      reload();
      Alert.alert('Profile saved', 'Your member profile has been updated.');
    } catch (error) {
      Alert.alert('Unable to save', error instanceof Error ? error.message : 'Please try again.');
    } finally {
      setBusy(false);
    }
  }

  const completion = data.member.profile_completion;
  const editable = ['phone', 'address', 'city', 'state', 'country', 'occupation', 'date_of_birth', 'emergency_contact_name', 'emergency_contact_phone', 'next_of_kin'];

  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Profile Readiness</Text>
        <View style={styles.progressShell}>
          <View style={[styles.progressFill, { width: `${completion.percent}%` }]} />
        </View>
        <Text style={styles.rowSub}>{completion.percent}% complete</Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Update Details</Text>
        {editable.map((field) => (
          <View key={field} style={styles.fieldBlock}>
            <Text style={styles.fieldLabel}>{field.replace(/_/g, ' ')}</Text>
            <TextInput
              value={profile[field] || ''}
              onChangeText={(value) => setProfile((current) => ({ ...current, [field]: value }))}
              style={styles.inputLight}
            />
          </View>
        ))}
        <Pressable style={[styles.primaryButton, busy && styles.disabled]} onPress={save} disabled={busy}>
          <Text style={styles.primaryButtonText}>{busy ? 'Saving...' : 'Save Profile'}</Text>
        </Pressable>
      </View>
    </ScrollView>
  );
}

function SavingsScreen({ token }: { token: string }) {
  const [rows, setRows] = useState<SavingRow[]>([]);
  const [balance, setBalance] = useState(0);

  useEffect(() => {
    getSavings(token).then((response) => {
      setRows(response.rows);
      setBalance(response.balance);
    }).catch(() => undefined);
  }, [token]);

  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <StatCard label="Savings Balance" value={money(balance)} />
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Savings Statement</Text>
        {rows.map((row) => (
          <View key={row.id} style={styles.listRow}>
            <Text style={styles.rowTitle}>{money(row.amount)}</Text>
            <Text style={styles.rowSub}>{row.month} - {row.receipt_number || 'No receipt'} - {row.date?.slice(0, 10) || ''}</Text>
          </View>
        ))}
        {rows.length === 0 ? <Text style={styles.emptyText}>No savings records found.</Text> : null}
      </View>
    </ScrollView>
  );
}

function LoansScreen({ token, dashboard, reload }: { token: string; dashboard: DashboardPayload; reload: () => void }) {
  const [selected, setSelected] = useState<Loan | null>(null);
  const [reason, setReason] = useState('');
  const [showApply, setShowApply] = useState(false);
  const [amount, setAmount] = useState('');
  const [purpose, setPurpose] = useState('Regular');
  const [tenure, setTenure] = useState('6');
  const [signature, setSignature] = useState('');
  const [collateral, setCollateral] = useState('standing_order');
  const [guarantors, setGuarantors] = useState('');
  const [preview, setPreview] = useState<{ monthly_payment: number; total_repayment: number; total_interest: number } | null>(null);
  const [busyApply, setBusyApply] = useState(false);

  async function openLoan(loan: Loan) {
    const response = await getLoanDetail(token, loan.id);
    setSelected(response.loan);
  }

  async function withdraw() {
    if (!selected) return;
    try {
      const response = await withdrawLoan(token, selected.id, reason);
      setSelected(response.loan);
      setReason('');
      await reload();
      Alert.alert('Application withdrawn', 'The loan application has left the approval workflow.');
    } catch (error) {
      Alert.alert('Unable to withdraw', error instanceof Error ? error.message : 'Please try again.');
    }
  }

  async function runPreview() {
    try {
      const response = await previewLoanSchedule(token, {
        amount: Number(amount),
        purpose,
        tenure: Number(tenure)
      });
      setPreview({
        monthly_payment: response.monthly_payment,
        total_repayment: response.total_repayment,
        total_interest: response.total_interest
      });
    } catch (error) {
      Alert.alert('Unable to preview', error instanceof Error ? error.message : 'Check the amount and tenure.');
    }
  }

  async function submitLoan() {
    if (!preview) {
      Alert.alert('Preview required', 'Preview and accept the repayment schedule before submitting.');
      return;
    }
    setBusyApply(true);
    try {
      await applyForLoan(token, {
        amount: Number(amount),
        purpose,
        tenure: Number(tenure),
        payment_collateral_type: collateral,
        guarantor_ids: guarantors.split(',').map((value) => value.trim()).filter(Boolean),
        signature_name: signature,
        accept_terms: true,
        data_processing_consent: true,
        repayment_schedule_accepted: true,
        hr_affordability_consent: true,
        credit_check_consent: true,
        bank_statement_ack: true
      });
      setShowApply(false);
      setPreview(null);
      setAmount('');
      setSignature('');
      setGuarantors('');
      reload();
      Alert.alert('Application submitted', 'Your loan request has entered the approval workflow.');
    } catch (error) {
      Alert.alert('Unable to submit', error instanceof Error ? error.message : 'Please try again.');
    } finally {
      setBusyApply(false);
    }
  }

  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <Pressable style={styles.primaryButton} onPress={() => setShowApply((value) => !value)}>
        <Text style={styles.primaryButtonText}>{showApply ? 'Close Application Form' : 'Apply for Loan'}</Text>
      </Pressable>

      {showApply ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>New Loan Application</Text>
          <Text style={styles.rowSub}>Enter the loan details, preview the schedule, then submit when you accept the repayment terms.</Text>
          <View style={styles.fieldBlock}>
            <Text style={styles.fieldLabel}>Amount</Text>
            <TextInput value={amount} onChangeText={setAmount} keyboardType="numeric" style={styles.inputLight} placeholder="50000" />
          </View>
          <View style={styles.fieldBlock}>
            <Text style={styles.fieldLabel}>Purpose</Text>
            <TextInput value={purpose} onChangeText={setPurpose} style={styles.inputLight} placeholder="Regular" />
          </View>
          <View style={styles.fieldBlock}>
            <Text style={styles.fieldLabel}>Tenure months</Text>
            <TextInput value={tenure} onChangeText={setTenure} keyboardType="numeric" style={styles.inputLight} placeholder="6" />
          </View>
          <View style={styles.fieldBlock}>
            <Text style={styles.fieldLabel}>Collateral</Text>
            <TextInput value={collateral} onChangeText={setCollateral} style={styles.inputLight} placeholder="standing_order or post_dated_cheques" />
          </View>
          <View style={styles.fieldBlock}>
            <Text style={styles.fieldLabel}>Guarantor IDs</Text>
            <TextInput value={guarantors} onChangeText={setGuarantors} style={styles.inputLight} placeholder="e.g. 3, 4" />
          </View>
          <Pressable style={styles.secondaryButton} onPress={runPreview}>
            <Text style={styles.secondaryButtonText}>Preview Schedule</Text>
          </Pressable>
          {preview ? (
            <View style={styles.previewBox}>
              <Text style={styles.detailLine}>Monthly payment: {money(preview.monthly_payment)}</Text>
              <Text style={styles.detailLine}>Total repayment: {money(preview.total_repayment)}</Text>
              <Text style={styles.detailLine}>Total interest: {money(preview.total_interest)}</Text>
            </View>
          ) : null}
          <View style={styles.fieldBlock}>
            <Text style={styles.fieldLabel}>Signature name</Text>
            <TextInput value={signature} onChangeText={setSignature} style={styles.inputLight} placeholder={dashboard.member.full_name} />
          </View>
          <Text style={styles.disclaimer}>
            By submitting, you accept the repayment schedule and permit the cooperative to process required application, affordability, and collateral checks.
          </Text>
          <Pressable style={[styles.primaryButton, busyApply && styles.disabled]} onPress={submitLoan} disabled={busyApply}>
            <Text style={styles.primaryButtonText}>{busyApply ? 'Submitting...' : 'Submit Application'}</Text>
          </Pressable>
        </View>
      ) : null}

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Loans</Text>
        {dashboard.loans.map((loan) => (
          <Pressable key={loan.id} style={styles.listRow} onPress={() => openLoan(loan)}>
            <View style={styles.rowBetween}>
              <Text style={styles.rowTitle}>{loan.loan_number}</Text>
              <Text style={[styles.statusPill, loan.status === 'active' && styles.statusActive]}>{loan.status}</Text>
            </View>
            <Text style={styles.rowSub}>{loan.purpose} - {money(loan.amount)} - {loan.tenure} months</Text>
          </Pressable>
        ))}
        {dashboard.loans.length === 0 ? <Text style={styles.emptyText}>No loan applications yet.</Text> : null}
      </View>

      {selected ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>{selected.loan_number}</Text>
          <Text style={styles.detailLine}>Amount: {money(selected.amount)}</Text>
          <Text style={styles.detailLine}>Total repayment: {money(selected.total_repayment)}</Text>
          <Text style={styles.detailLine}>Balance: {selected.is_disbursed ? money(selected.balance) : 'Not disbursed'}</Text>
          <Text style={styles.detailLine}>Status: {selected.status}</Text>
          {selected.status === 'pending' ? (
            <>
              <TextInput
                value={reason}
                onChangeText={setReason}
                placeholder="Reason for withdrawal"
                style={styles.inputLight}
              />
              <Pressable style={styles.dangerButton} onPress={withdraw}>
                <Text style={styles.dangerButtonText}>Withdraw Application</Text>
              </Pressable>
            </>
          ) : null}
        </View>
      ) : null}
    </ScrollView>
  );
}

function NotificationsScreen({ token, initial }: { token: string; initial: MobileNotification[] }) {
  const [items, setItems] = useState(initial);

  async function refresh() {
    const response = await getNotifications(token);
    setItems(response.notifications);
  }

  async function markAll() {
    await markAllNotificationsRead(token);
    await refresh();
  }

  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <View style={styles.card}>
        <View style={styles.rowBetween}>
          <Text style={styles.cardTitle}>Notifications</Text>
          <Pressable onPress={markAll}>
            <Text style={styles.linkText}>Mark all read</Text>
          </Pressable>
        </View>
        {items.map((item) => (
          <View key={item.id} style={styles.listRow}>
            <Text style={styles.rowTitle}>{item.title}</Text>
            <Text style={styles.rowSub}>{item.message}</Text>
          </View>
        ))}
        {items.length === 0 ? <Text style={styles.emptyText}>No notifications yet.</Text> : null}
      </View>
    </ScrollView>
  );
}

function TabBar({ active, setActive, unread }: { active: Tab; setActive: (tab: Tab) => void; unread: number }) {
  const tabs: Array<{ key: Tab; icon: keyof typeof Ionicons.glyphMap; label: string }> = [
    { key: 'home', icon: 'home', label: 'Home' },
    { key: 'profile', icon: 'person', label: 'Profile' },
    { key: 'savings', icon: 'wallet', label: 'Savings' },
    { key: 'loans', icon: 'cash', label: 'Loans' },
    { key: 'notifications', icon: 'notifications', label: 'Alerts' }
  ];
  return (
    <View style={styles.tabBar}>
      {tabs.map((tab) => (
        <Pressable key={tab.key} style={styles.tabButton} onPress={() => setActive(tab.key)}>
          <Ionicons name={tab.icon} size={21} color={active === tab.key ? NAVY : '#7D8797'} />
          <Text style={[styles.tabLabel, active === tab.key && styles.tabActive]}>{tab.label}</Text>
          {tab.key === 'notifications' && unread > 0 ? <View style={styles.dot} /> : null}
        </Pressable>
      ))}
    </View>
  );
}

export default function App() {
  const [token, setToken] = useState<string | null>(null);
  const [dashboard, setDashboard] = useState<DashboardPayload | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>('home');
  const [loading, setLoading] = useState(true);

  async function reload(currentToken = token) {
    if (!currentToken) return;
    const data = await getDashboard(currentToken);
    setDashboard(data);
  }

  useEffect(() => {
    loadToken().then(async (stored) => {
      if (stored) {
        setToken(stored);
        try {
          await reload(stored);
          void tryRegisterPushToken(stored);
        } catch {
          await clearToken();
          setToken(null);
        }
      }
    }).finally(() => setLoading(false));
  }, []);

  async function onLogin(nextToken: string) {
    setToken(nextToken);
    await reload(nextToken);
  }

  async function logout() {
    await clearToken();
    setToken(null);
    setDashboard(null);
    setActiveTab('home');
  }

  const title = useMemo(() => {
    if (activeTab === 'home') return 'Dashboard';
    if (activeTab === 'profile') return 'Profile';
    if (activeTab === 'savings') return 'Savings';
    if (activeTab === 'loans') return 'Loans';
    return 'Notifications';
  }, [activeTab]);

  if (loading) {
    return (
      <SafeAreaView style={styles.loadingRoot}>
        <ActivityIndicator size="large" color={NAVY} />
      </SafeAreaView>
    );
  }

  if (!token || !dashboard) {
    return <LoginScreen onLogin={onLogin} />;
  }

  return (
    <SafeAreaView style={styles.appRoot}>
      <StatusBar barStyle="dark-content" />
      <Header title={title} onLogout={logout} />
      {activeTab === 'home' ? <HomeScreen data={dashboard} reload={() => reload()} /> : null}
      {activeTab === 'profile' ? <ProfileScreen token={token} data={dashboard} reload={() => reload()} /> : null}
      {activeTab === 'savings' ? <SavingsScreen token={token} /> : null}
      {activeTab === 'loans' ? <LoansScreen token={token} dashboard={dashboard} reload={() => reload()} /> : null}
      {activeTab === 'notifications' ? <NotificationsScreen token={token} initial={dashboard.notifications} /> : null}
      <TabBar active={activeTab} setActive={setActiveTab} unread={dashboard.summary.unread_notifications} />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  appRoot: { flex: 1, backgroundColor: BG },
  loadingRoot: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: BG },
  authRoot: { flex: 1, backgroundColor: NAVY },
  authContent: { flex: 1, justifyContent: 'center', padding: 24 },
  logoMark: { width: 72, height: 72, borderRadius: 20, backgroundColor: '#fff', alignItems: 'center', justifyContent: 'center', marginBottom: 18 },
  brand: { color: '#fff', fontSize: 34, fontWeight: '900', letterSpacing: 0 },
  tagline: { color: '#DDE8FF', fontSize: 15, marginTop: 6, marginBottom: 28 },
  loginCard: { backgroundColor: '#fff', borderRadius: 8, padding: 18, shadowColor: '#000', shadowOpacity: 0.12, shadowRadius: 18, elevation: 4 },
  loginTitle: { color: INK, fontSize: 20, fontWeight: '800', marginBottom: 14 },
  input: { borderWidth: 1, borderColor: '#D7DEE8', borderRadius: 8, padding: 14, marginBottom: 12, fontSize: 16, color: INK },
  inputLight: { borderWidth: 1, borderColor: '#D7DEE8', borderRadius: 8, padding: 12, marginTop: 6, marginBottom: 12, fontSize: 15, color: INK, backgroundColor: '#fff' },
  primaryButton: { backgroundColor: BLUE, borderRadius: 8, padding: 15, alignItems: 'center', justifyContent: 'center' },
  primaryButtonText: { color: '#fff', fontWeight: '800', fontSize: 16 },
  secondaryButton: { backgroundColor: '#EEF4FF', borderRadius: 8, padding: 14, alignItems: 'center', marginTop: 4, marginBottom: 10 },
  secondaryButtonText: { color: BLUE, fontWeight: '900' },
  dangerButton: { backgroundColor: '#D93030', borderRadius: 8, padding: 14, alignItems: 'center', marginTop: 4 },
  dangerButtonText: { color: '#fff', fontWeight: '800' },
  disabled: { opacity: 0.7 },
  apiHint: { color: '#667085', marginTop: 12, fontSize: 12 },
  header: { paddingHorizontal: 18, paddingVertical: 14, backgroundColor: '#fff', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderBottomWidth: 1, borderColor: '#E4E9F0' },
  headerEyebrow: { color: YELLOW, fontWeight: '900', fontSize: 12 },
  headerTitle: { color: NAVY, fontWeight: '900', fontSize: 24 },
  iconButton: { width: 42, height: 42, borderRadius: 8, backgroundColor: '#EEF4FF', alignItems: 'center', justifyContent: 'center' },
  screen: { padding: 16, paddingBottom: 104 },
  memberBand: { backgroundColor: NAVY, borderRadius: 8, padding: 18, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  memberName: { color: '#fff', fontSize: 20, fontWeight: '900' },
  memberMeta: { color: '#CFE0FF', marginTop: 4 },
  certifiedBadge: { backgroundColor: YELLOW, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 6, flexDirection: 'row', gap: 4, alignItems: 'center' },
  certifiedText: { color: NAVY, fontWeight: '900', fontSize: 12 },
  statGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginVertical: 14 },
  statCard: { flexBasis: '48%', flexGrow: 1, backgroundColor: '#fff', borderRadius: 8, padding: 14, borderWidth: 1, borderColor: '#E2E8F0' },
  statYellow: { borderColor: '#F5D66D', backgroundColor: '#FFF8E5' },
  statGreen: { borderColor: '#A7E0C2', backgroundColor: '#EDFFF4' },
  statLabel: { color: '#667085', fontSize: 12, fontWeight: '800', textTransform: 'uppercase' },
  statValue: { color: INK, fontSize: 17, fontWeight: '900', marginTop: 8 },
  card: { backgroundColor: '#fff', borderRadius: 8, marginTop: 14, borderWidth: 1, borderColor: '#E2E8F0', overflow: 'hidden', padding: 14 },
  cardTitle: { fontSize: 17, color: INK, fontWeight: '900', marginBottom: 8 },
  rowBetween: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  listRow: { paddingVertical: 12, borderTopWidth: 1, borderColor: '#EEF2F6' },
  rowTitle: { color: INK, fontWeight: '800', fontSize: 15 },
  rowSub: { color: '#667085', marginTop: 4, lineHeight: 19 },
  emptyText: { color: '#667085', paddingVertical: 12 },
  progressShell: { height: 10, borderRadius: 999, backgroundColor: '#E5EAF2', overflow: 'hidden', marginTop: 10, marginBottom: 6 },
  progressFill: { height: 10, backgroundColor: YELLOW },
  fieldBlock: { marginTop: 6 },
  fieldLabel: { color: '#667085', fontWeight: '800', textTransform: 'capitalize' },
  detailLine: { color: INK, marginBottom: 8, fontWeight: '600' },
  disclaimer: { color: '#667085', fontSize: 12, lineHeight: 18, marginBottom: 12 },
  previewBox: { borderWidth: 1, borderColor: '#D7E7FF', backgroundColor: '#F4F8FF', borderRadius: 8, padding: 12, marginBottom: 10 },
  statusPill: { color: '#fff', backgroundColor: '#7D8797', overflow: 'hidden', borderRadius: 999, paddingHorizontal: 9, paddingVertical: 4, fontSize: 12, textTransform: 'capitalize' },
  statusActive: { backgroundColor: '#159A5B' },
  linkText: { color: BLUE, fontWeight: '800' },
  tabBar: { position: 'absolute', bottom: 0, left: 0, right: 0, height: 78, backgroundColor: '#fff', flexDirection: 'row', borderTopWidth: 1, borderColor: '#E4E9F0', paddingBottom: 10 },
  tabButton: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 4 },
  tabLabel: { color: '#7D8797', fontSize: 11, fontWeight: '700' },
  tabActive: { color: NAVY },
  dot: { position: 'absolute', top: 15, right: 22, width: 8, height: 8, borderRadius: 999, backgroundColor: '#D93030' }
});
