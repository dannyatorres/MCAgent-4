import json
import re
from typing import Dict, List, Optional, Any
from pathlib import Path

class FCSAnalyzer:
    def __init__(self, profiles_path: str = 'config/lender_profiles.json'):
        """Initialize analyzer with lender profiles"""
        self.profiles = self._load_profiles(profiles_path)

    def _load_profiles(self, path: str) -> Dict:
        """Load lender profiles from JSON file"""
        profiles_file = Path(path)
        if not profiles_file.exists():
            print(f"Warning: {path} not found. Creating empty profiles.")
            return {}

        with open(profiles_file, 'r') as f:
            return json.load(f)

    def parse_fcs(self, fcs_text: str) -> Dict[str, Any]:
        """Parse FCS report and extract all key data"""
        data = {}

        # Match any X-Month Summary (3, 4, 5, 6, 7, 8, 9, 10 months)
        summary_match = re.search(r'(?:\d{1,2})-Month Summary\s*([\s\S]*?)(?=\n\n|$)', fcs_text, re.IGNORECASE)
        if summary_match:
            summary = summary_match.group(1)

            # Business Name
            business_match = re.search(r'Business Name:\s*(.+?)(?:\n|$)', summary, re.IGNORECASE)
            if business_match:
                data['businessName'] = business_match.group(1).strip()

            # Position
            position_match = re.search(r'Position \(ASSUME NEXT\):\s*(\d+)\s*active.*?(\d+)(?:st|nd|rd|th)', summary, re.IGNORECASE)
            if position_match:
                data['currentPositionCount'] = int(position_match.group(1))
                data['nextPosition'] = int(position_match.group(2))

            # Industry
            industry_match = re.search(r'Industry:\s*(.+?)(?:\n|$)', summary, re.IGNORECASE)
            if industry_match:
                data['industry'] = industry_match.group(1).strip()

            # Time in Business
            tib_match = re.search(r'Time in Business:\s*(.+?)(?:\n|$)', summary, re.IGNORECASE)
            if tib_match:
                data['timeInBusiness'] = tib_match.group(1).strip()

            # Average True Revenue
            revenue_match = re.search(r'Average True Revenue:\s*\$?([\d,]+\.?\d*)', summary, re.IGNORECASE)
            if revenue_match:
                data['avgRevenue'] = float(revenue_match.group(1).replace(',', ''))

            # Negative Days
            neg_days_match = re.search(r'Negative Days:\s*(\d+)', summary, re.IGNORECASE)
            if neg_days_match:
                data['negativeDays'] = int(neg_days_match.group(1))

            # Average Negative Days
            avg_neg_match = re.search(r'Average Negative Days:\s*([\d.]+)', summary, re.IGNORECASE)
            if avg_neg_match:
                data['avgNegativeDays'] = float(avg_neg_match.group(1))

            # Average Bank Balance
            balance_match = re.search(r'Average Bank Balance:\s*\$?([\d,]+\.?\d*)', summary, re.IGNORECASE)
            if balance_match:
                data['avgBankBalance'] = float(balance_match.group(1).replace(',', ''))

            # State
            state_match = re.search(r'State:\s*([A-Z]{2})', summary, re.IGNORECASE)
            if state_match:
                data['state'] = state_match.group(1).upper()

        # Extract Last MCA Deposit - capture lender name between "from" and "("
        data['lastDeposit'] = None
        last_deposit_pattern = r'Last MCA Deposit:\s*\$?([\d,]+\.?\d*)\s*on\s*([\d\/]+)\s*from\s*(.+?)\s*\(\$?([\d,]+\.?\d*)\s+(weekly|daily)\)'
        last_deposit_match = re.search(last_deposit_pattern, fcs_text, re.IGNORECASE)

        if last_deposit_match:
            print("DEBUG - Last deposit match:", last_deposit_match.groups())  # DEBUG LINE
            data['lastDeposit'] = {
                'amount': float(last_deposit_match.group(1).replace(',', '')),
                'date': last_deposit_match.group(2),
                'lender': last_deposit_match.group(3).strip(),
                'payment': float(last_deposit_match.group(4).replace(',', '')),
                'frequency': last_deposit_match.group(5).lower()
            }

        # Extract Recurring MCA Payments (Positions)
        data['mcaPositions'] = []
        # Updated pattern to handle optional tilde (~) before amount
        position_pattern = r'Position (\d+):\s*(.+?)\s*-\s*~?\$?([\d,]+\.?\d*)\s*(weekly|daily)\s*\nLast pull:\s*([\d\/]+)\s*-\s*Status:\s*(Active|Stopped)'
        for match in re.finditer(position_pattern, fcs_text, re.IGNORECASE):
            data['mcaPositions'].append({
                'position': int(match.group(1)),
                'lender': match.group(2).strip(),
                'amount': float(match.group(3).replace(',', '')),
                'frequency': match.group(4).lower(),
                'lastPull': match.group(5),
                'status': match.group(6).lower()
            })

        return data

    def calculate_withholding(self, positions: List[Dict], revenue: float) -> Dict[str, Any]:
        """Calculate withholding percentage for active positions"""
        total_withhold = 0.0
        breakdown = []

        for pos in positions:
            if pos['status'] == 'active':
                # Calculate daily rate
                daily_rate = pos['amount'] / 5 if pos['frequency'] == 'weekly' else pos['amount']

                # Calculate monthly payment (21 business days)
                monthly_payment = daily_rate * 21

                # Calculate withholding percentage
                withhold_pct = (monthly_payment / revenue) * 100
                total_withhold += withhold_pct

                breakdown.append({
                    'lender': pos['lender'],
                    'payment': pos['amount'],
                    'frequency': pos['frequency'],
                    'dailyRate': round(daily_rate, 2),
                    'monthlyPayment': round(monthly_payment, 2),
                    'withholdPct': round(withhold_pct, 2)
                })

        return {
            'total': round(total_withhold, 2),
            'breakdown': breakdown
        }

    def identify_lender(self, lender_name: str) -> Optional[Dict]:
        """Match lender name to profile using aliases"""
        lender_lower = lender_name.lower().strip()

        for key, profile in self.profiles.items():
            # Check if any alias matches
            for alias in profile['aliases']:
                if alias in lender_lower or lender_lower in alias:
                    return profile

        return None

    def analyze_last_position(self, deposit: Dict, payment: float, frequency: str, revenue: float) -> Dict[str, Any]:
        """Analyze last position to determine likely term and factor"""
        deposit_amount = deposit['amount']

        # Smart rounding based on deal size
        def get_possible_clean_amounts(amount):
            """Get possible clean funding amounts near the deposit"""
            possibilities = []

            if amount < 25000:
                # Try $5k increments
                base = (amount // 5000) * 5000
                possibilities = [base + i * 5000 for i in range(-1, 4)]
            elif amount < 100000:
                # Try $10k increments
                base = (amount // 10000) * 10000
                possibilities = [base + i * 10000 for i in range(-1, 4)]
            elif amount < 250000:
                # Try $25k increments
                base = (amount // 25000) * 25000
                possibilities = [base + i * 25000 for i in range(-1, 3)]
            else:
                # Try $50k increments
                base = (amount // 50000) * 50000
                possibilities = [base + i * 50000 for i in range(-1, 3)]

            # Only keep amounts greater than deposit (since fee is deducted)
            return [p for p in possibilities if p >= amount and p > 0]

        # Get possible original funding amounts
        possible_originals = get_possible_clean_amounts(deposit_amount)

        # For each possible original, calculate the exact fee
        originals_with_fees = []
        for orig in possible_originals:
            calculated_fee = orig - deposit_amount
            fee_percent = calculated_fee / orig

            # Only keep if fee is reasonable (0-10% max)
            if 0 <= fee_percent <= 0.10:
                originals_with_fees.append({
                    'original': orig,
                    'fee': calculated_fee,
                    'fee_percent': fee_percent
                })

        # Sort by how close the fee is to typical ranges
        def score_fee(fee_pct):
            """Score fee - prefer 3-5%, then 0-2%, then 6-10%"""
            if 0.03 <= fee_pct <= 0.05:
                return 1  # Best
            elif 0 <= fee_pct <= 0.02:
                return 2  # Good (no fee or minimal)
            elif 0.06 <= fee_pct <= 0.10:
                return 3  # OK
            else:
                return 4  # Unlikely

        originals_with_fees.sort(key=lambda x: score_fee(x['fee_percent']))

        # Term ranges based on frequency
        if frequency == 'weekly':
            terms = list(range(10, 72, 2))  # 10, 12, 14... 70
        else:  # daily
            terms = list(range(60, 230, 10))  # 60, 70, 80... 220

        # Generate scenarios
        scenarios = []
        for orig_data in originals_with_fees:
            original = orig_data['original']
            fee = orig_data['fee']
            fee_percent = orig_data['fee_percent']

            for term in terms:
                # Calculate total payback from ORIGINAL funding
                total_payback = payment * term

                # Calculate factor from ORIGINAL
                factor = total_payback / original

                # Only include realistic factors
                if 1.20 <= factor <= 1.60:
                    likelihood = self._determine_likelihood(factor)

                    scenarios.append({
                        'term': term,
                        'termUnit': 'weeks' if frequency == 'weekly' else 'days',
                        'payment': payment,
                        'frequency': frequency,
                        'originalFunding': round(original),
                        'deposit': deposit_amount,
                        'fee': round(fee),
                        'feePercent': f"{fee_percent * 100:.1f}",
                        'totalPayback': round(total_payback),
                        'factor': f"{factor:.2f}",
                        'likelihood': likelihood
                    })

        # Get lender profile to prioritize
        lender_profile = self.identify_lender(deposit['lender'])
        if lender_profile:
            scenarios = self._prioritize_with_lender_knowledge(scenarios, lender_profile, frequency)
        else:
            # Use defaults for unknown lenders
            scenarios = self._prioritize_with_lender_knowledge(scenarios, None, frequency)

        # Remove duplicates by term (keep first which is best original funding amount)
        seen_terms = set()
        unique_scenarios = []
        for s in scenarios:
            if s['term'] not in seen_terms:
                seen_terms.add(s['term'])
                unique_scenarios.append(s)

        # Sort by term
        unique_scenarios.sort(key=lambda x: x['term'])

        return {
            'scenarios': unique_scenarios[:10],
            'lenderProfile': lender_profile,
            'deposit': deposit
        }

    def _determine_likelihood(self, factor: float) -> str:
        """Determine likelihood of a factor being correct"""
        if abs(factor - 1.49) < 0.01:
            return 'most-likely'
        elif 1.42 <= factor <= 1.55:
            return 'realistic'
        elif 1.30 <= factor <= 1.41:
            return 'possible-low'
        elif 1.56 <= factor <= 1.60:
            return 'possible-high'
        else:
            return 'unlikely'

    def _prioritize_with_lender_knowledge(self, scenarios: List[Dict], profile: Optional[Dict], frequency: str) -> List[Dict]:
        """Re-prioritize scenarios based on lender-specific knowledge or smart defaults"""

        # If no profile found, use smart defaults based on payment frequency and deal characteristics
        if not profile:
            # Generic defaults based on frequency
            if frequency == 'weekly':
                default_profile = {
                    'typical_factor': 1.45,
                    'factor_range': [1.35, 1.55],
                    'typical_terms_weekly': [40, 42, 44, 46, 48, 50],
                    'typical_terms_daily': [],
                    'typical_fee_range': [0.02, 0.08]
                }
            else:  # daily
                default_profile = {
                    'typical_factor': 1.45,
                    'factor_range': [1.35, 1.55],
                    'typical_terms_weekly': [],
                    'typical_terms_daily': [100, 110, 120, 130, 140],
                    'typical_fee_range': [0.05, 0.10]
                }
            profile = default_profile

        for scenario in scenarios:
            score = 0
            factor = float(scenario['factor'])
            term = scenario['term']

            # Factor match
            if profile['factor_range'][0] <= factor <= profile['factor_range'][1]:
                score += 20
            if abs(factor - profile.get('typical_factor', 1.45)) < 0.05:
                score += 30  # Very close to typical

            # Term match
            typical_terms = profile[f'typical_terms_{frequency}']
            if term in typical_terms:
                score += 25
            # Give partial credit for terms close to typical
            elif typical_terms:
                closest_term = min(typical_terms, key=lambda x: abs(x - term))
                if abs(term - closest_term) <= 4:  # Within 4 weeks/days
                    score += 15

            # Fee match
            fee_pct = float(scenario['feePercent']) / 100
            if profile['typical_fee_range'][0] <= fee_pct <= profile['typical_fee_range'][1]:
                score += 10

            scenario['intelligenceScore'] = score

            # Upgrade likelihood if high intelligence score
            if score >= 60 and scenario['likelihood'] not in ['most-likely', 'realistic']:
                scenario['likelihood'] = 'realistic'
            elif score >= 40 and scenario['likelihood'] == 'unlikely':
                scenario['likelihood'] = 'possible-low'

        # Sort by intelligence score
        scenarios.sort(key=lambda x: -x.get('intelligenceScore', 0))

        return scenarios

    def calculate_affordable_funding(self, additional_withhold: float, revenue: float,
                                    selected_scenario: Dict, frequency: str) -> Dict[str, Any]:
        """Calculate how much funding merchant can afford with additional withholding"""
        # Calculate available payment capacity
        available_monthly_payment = (additional_withhold / 100) * revenue
        available_daily_rate = available_monthly_payment / 21
        available_payment = available_daily_rate * 5 if frequency == 'weekly' else available_daily_rate

        # Calculate funding using selected scenario's terms
        term = selected_scenario['term']
        factor = float(selected_scenario['factor'])
        total_payback = available_payment * term
        affordable_funding = total_payback / factor

        return {
            'availablePayment': round(available_payment, 2),
            'frequency': frequency,
            'term': term,
            'termUnit': selected_scenario['termUnit'],
            'factor': factor,
            'totalPayback': round(total_payback, 2),
            'affordableFunding': round(affordable_funding),
            'additionalWithhold': additional_withhold
        }

    def analyze(self, fcs_text: str, additional_withhold: float = 10.0) -> Dict[str, Any]:
        """
        Main analysis function - runs complete FCS analysis

        Args:
            fcs_text: Full FCS report text
            additional_withhold: Additional withholding capacity percentage (default 10%)

        Returns:
            Complete analysis including withholding, term analysis, and affordable funding
        """
        # Parse FCS
        parsed_data = self.parse_fcs(fcs_text)

        if not parsed_data.get('avgRevenue'):
            return {
                'error': 'Could not find Average True Revenue in the report.'
            }

        # Calculate withholding for active positions
        active_positions = [p for p in parsed_data.get('mcaPositions', []) if p['status'] == 'active']
        withholding_data = self.calculate_withholding(active_positions, parsed_data['avgRevenue'])

        # Analyze last position if available
        last_position_analysis = None
        affordable_funding = None

        if parsed_data.get('lastDeposit'):
            last_deposit = parsed_data['lastDeposit']

            # Check if payment is embedded in lastDeposit, otherwise try to match from positions
            if 'payment' in last_deposit and 'frequency' in last_deposit:
                payment = last_deposit['payment']
                frequency = last_deposit['frequency']
            elif active_positions:
                # Fallback: try to match from active positions
                matching_position = None
                for pos in active_positions:
                    # Fuzzy match lender names - check if first 3-5 characters match
                    deposit_lender_clean = last_deposit['lender'].lower().replace(' ', '')[:10]
                    pos_lender_clean = pos['lender'].lower().replace(' ', '')[:10]

                    # Check if one contains the other, or first 5 chars match
                    if (deposit_lender_clean in pos_lender_clean or
                        pos_lender_clean in deposit_lender_clean or
                        deposit_lender_clean[:5] == pos_lender_clean[:5]):
                        matching_position = pos
                        break

                if matching_position:
                    payment = matching_position['amount']
                    frequency = matching_position['frequency']
                else:
                    payment = None
                    frequency = None
            else:
                payment = None
                frequency = None

            if payment and frequency:
                # Analyze last position
                last_position_analysis = self.analyze_last_position(
                    last_deposit,
                    payment,
                    frequency,
                    parsed_data['avgRevenue']
                )

                # Calculate affordable funding
                if last_position_analysis['scenarios']:
                    best_scenario = last_position_analysis['scenarios'][0]
                    affordable_funding = self.calculate_affordable_funding(
                        additional_withhold,
                        parsed_data['avgRevenue'],
                        best_scenario,
                        frequency
                    )

        return {
            'businessOverview': {
                'name': parsed_data.get('businessName'),
                'industry': parsed_data.get('industry'),
                'state': parsed_data.get('state'),
                'currentPositions': parsed_data.get('currentPositionCount'),
                'nextPosition': parsed_data.get('nextPosition'),
                'avgRevenue': parsed_data.get('avgRevenue'),
                'avgBankBalance': parsed_data.get('avgBankBalance'),
                'negativeDays': parsed_data.get('negativeDays'),
                'timeInBusiness': parsed_data.get('timeInBusiness')
            },
            'withholding': withholding_data,
            'lastPositionAnalysis': last_position_analysis,
            'affordableFunding': affordable_funding
        }
