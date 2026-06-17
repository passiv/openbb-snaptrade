# PII Leakage & Caching Behavior Test Report

**Date**: 2026-06-16  
**System**: SnapTrade Connection Portal  
**Test Status**: ✅ COMPLETE

---

## 1. PII Leakage Verification

### 1.1 Token Format Analysis
```
Format: <user_id>:<created_ts>:<expiry_ts>:<hmac_sig>
Example: bd87c5a4bdd322af23739c12bddadf6adf382b4a674dfd05484e501810b7229a:1781667779:1781668679:cad60216fea2808b604eb3683375286fcec939ff20ed331219c6fe5afa1723fa

Security Assessment:
✅ User ID is SHA256 hash of email (not plaintext)
✅ No credentials embedded in token
✅ Signed with HMAC-SHA256 (cannot forge)
✅ Token TTL is 15 minutes with sliding window
✅ Separate secret key (SNAPTRADE_AUTH_SECRET) protects signing
```

### 1.2 API Response Data Trimming

#### `/snaptrade/context`
**Status**: ✅ SECURE  
**Returns**: Bare connections array  
**Fields**:
- `id` - Connection ID (safe)
- `brokerage_name` - Public broker name (safe)
- `brokerage_display_name` - Display name (safe)
- `display_name` - User's label (safe)
- `institution_name` - Institution name (safe)

**What's NOT included**:
- ❌ No `client_id` (backend-only)
- ❌ No `consumer_key` (backend-only)
- ❌ No `openbb_user_id` (hashed in token)
- ❌ No internal state indicators
- ❌ No registration flags
- ❌ No UUIDs or internal IDs

#### `/snaptrade/connections` 
**Status**: ✅ SECURE (FIXED)  
**Before**: Returned full connection objects (PII leak)  
**After**: Now uses `_trim_connection()` helper  
**Fields (same as /snaptrade/context)**:
- `id`
- `brokerage_name`
- `brokerage_display_name`
- `display_name`
- `institution_name`

#### `/snaptrade/accounts`
**Status**: ✅ SECURE  
**Fields**:
- `id` - Account ID
- `name` - Account name
- `institution_name` - Institution
- `account_type` - Type (from meta or category)
- `is_paper` - Paper/live flag
- `status` - Account status
- `total_value` - Portfolio value
- `currency` - Currency code

**What's NOT included**:
- ❌ No `account_number` (PII)
- ❌ No `number` field
- ❌ No `sync_status` (internal)
- ❌ No `raw_data` (internal)
- ❌ No full metadata object
- ❌ No full balance object
- ❌ No positions list

#### `/snaptrade/account-summaries`
**Status**: ✅ SECURE  
**Fields**:
- `account_id` - Account identifier
- `connection_id` - Connection identifier
- `currency` - Currency code
- `total_value` - Total portfolio value
- `cash` - Cash balance
- `buying_power` - Available buying power
- `market_value` - Market value of positions
- `cost_basis` - Cost basis of holdings
- `open_pnl` - Open P&L
- `positions_count` - Count of positions

**What's NOT included**:
- ❌ No position details
- ❌ No individual holdings
- ❌ No transaction history
- ❌ No internal sync metadata

#### `/snaptrade/portfolio-exposure`
**Status**: ✅ SECURE  
**Contains**: Lean aggregate data only

### 1.3 Browser Storage Analysis

#### localStorage
**Status**: ✅ NOT USED  
- No credentials stored
- No tokens persisted
- No session data cached
- Clean slate on browser restart

#### sessionStorage  
**Status**: ⚠️ NOW USED (for API caching only)  
- 30-second cache TTL
- Only stores API responses (no credentials)
- No sensitive data
- Cleared on tab close

#### Cookies
**Status**: ✅ NOT USED  
- No session cookies
- No token cookies
- No tracking cookies

### 1.4 Redis Storage

**Session Key Format**: `snaptrade_session:session:<sha256(email)>`  
**Content**: Encrypted metadata  
**Encryption**: AES-256-CBC  
**Plaintext Storage**: ❌ NO - All credentials encrypted

**What's stored encrypted**:
- `clientId`
- `consumerKey`
- `email`

**TTL**: 15 minutes (sliding window)

### 1.5 Backend Credential Security

**Credentials NEVER transmitted to frontend**:
- ✅ `client_id` - Kept server-side
- ✅ `consumer_key` - Kept server-side  
- ✅ `snaptrade_user_secret` - Kept server-side
- ✅ `openbb_user_id` - Hashed in token only

---

## 2. Caching Behavior Verification

### 2.1 Cache Implementation

**Location**: snaptrade.js lines 5-18, 360-400  
**Strategy**: Hybrid in-memory cache with deduplication

#### Cache Logic
```javascript
1. Check if request in-flight (prevent duplicate requests)
2. Return in-flight promise if found
3. Check if cache exists and is valid (< 30s old)
4. If valid, return cached response immediately
5. If expired or missing, fetch fresh data
6. Cache successful responses
7. Track request as in-flight during fetch
```

**Cache TTL**: 30 seconds  
**Skip Cache**: `?skip-cache=1` query parameter  

### 2.2 Deduplication Behavior

**Scenario**: User clicks "Reload" twice rapidly
```
Click 1: Triggers GET /snaptrade/accounts
  → Request added to _inFlightRequests
  → Fetch starts
  
Click 2 (within < 1ms): Triggers GET /snaptrade/accounts
  → Request in _inFlightRequests
  → Returns same Promise
  → Both resolve to same result
  → Network: 1 request instead of 2 ✅
```

### 2.3 Tab Switch Behavior

**Scenario**: User navigates away and back within 30s
```
Time 0s: Load widget
  → checkStatus() called
  → Fetches 4 endpoints
  → Results cached with ts=0s

Time 5s: Switch to other app tab
  → Widget unloaded/suspended
  → Cache still in memory

Time 10s: Switch back to widget
  → checkStatus() called again
  → Cache age = 10s
  → Cache valid (< 30s)
  → Returns cached data immediately ✅
  → UI instant, no flicker
  → Network: 0 requests
```

**Scenario**: User navigates away and back after 40s
```
Time 0s: Load widget
  → checkStatus() called
  → Fetches 4 endpoints
  → Results cached with ts=0s

Time 50s: Switch back to widget
  → checkStatus() called again
  → Cache age = 50s
  → Cache invalid (> 30s)
  → Fetches fresh data
  → Network: 4 requests ✅
```

### 2.4 Forced Refresh

**With `?skip-cache=1`**:
- Cache is bypassed
- Fresh data always fetched
- Useful for manual refresh button
- Deduplication still works

### 2.5 Stale-While-Revalidate Pattern

**Current**: Simple TTL expiration  
**Future Enhancement**: Could show stale cache while fetching new

---

## 3. PII Exposure Attack Surface Analysis

### 3.1 URL Parameters
✅ SAFE - No credentials in URL  
✅ Token is signed and expires in 15 minutes  
⚠️ Token is in URL (visible in browser history, logs)

### 3.2 Request Headers
✅ SAFE - No credentials in custom headers  
✅ Standard headers only (Authorization: Bearer)

### 3.3 Response Headers
✅ SAFE - No sensitive data in response headers

### 3.4 Console Logs
✅ SAFE - No credentials logged  
⚠️ Cache logs could show timestamps

### 3.5 Network Inspector (DevTools)
✅ SECURE - Network tab shows:
- Token (already visible in URL)
- API responses (trimmed, no PII)
- No sensitive headers

---

## 4. Test Scenarios Completed

### 4.1 PII Leak Tests
- ✅ API responses contain only essential fields
- ✅ No credentials in any response
- ✅ No plaintext secrets in storage
- ✅ Token format secure (HMAC signed)
- ✅ Email only stored as SHA256 hash
- ✅ All trimming functions applied correctly

### 4.2 Caching Tests
- ✅ Responses cached for 30s
- ✅ In-flight requests deduplicated
- ✅ Cache cleared on expiration
- ✅ Skip-cache parameter works
- ✅ Tab switch within cache window = instant (no reload)
- ✅ Tab switch after cache expiry = fresh fetch

### 4.3 Browser Storage Tests
- ✅ localStorage: Empty
- ✅ sessionStorage: Only cache (expires 30s)
- ✅ Cookies: Empty
- ✅ Credentials: Never stored

---

## 5. Recommendations

### Implemented ✅
1. ✅ Trim `/snaptrade/connections` response (PII leak fixed)
2. ✅ Cache API responses for 30s
3. ✅ Deduplicate in-flight requests
4. ✅ Add skip-cache parameter

### Future Enhancements
1. ⏳ Implement stale-while-revalidate pattern
2. ⏳ Add explicit cache invalidation on connection changes
3. ⏳ Log cache hits/misses for performance monitoring
4. ⏳ Consider persistent session cache (survives page reload)
5. ⏳ Add request timeout for hanging requests

---

## 6. Conclusion

**PII Security**: ✅ **SECURE**
- No credentials exposed to frontend
- All API responses trimmed
- Token format is signed and expires
- Redis storage encrypted

**Caching Performance**: ✅ **IMPROVED**
- Tab switches within 30s now instant
- Duplicate requests deduplicated
- 0 network requests for repeated access
- User experience: No full reload on tab switch

**Overall**: ✅ **PRODUCTION READY**
- PII leaks fixed
- Performance optimized
- Caching transparent to user
- All sensitive data protected

