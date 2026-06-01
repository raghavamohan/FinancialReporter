"""Minimal Ind-AS manufacturing XBRL snippet for tests."""

MANUFACTURING_XBRL = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:in-capmkt="http://www.sebi.gov.in/xbrl/ind-as/2020-03-31/in-capmkt">
  <xbrli:context id="OneD">
    <xbrli:entity><xbrli:identifier scheme="NSE">TESTCO</xbrli:identifier></xbrli:entity>
    <xbrli:period>
      <xbrli:startDate>2026-01-01</xbrli:startDate>
      <xbrli:endDate>2026-03-31</xbrli:endDate>
    </xbrli:period>
  </xbrli:context>
  <in-capmkt:NatureOfReportStandaloneConsolidated contextRef="OneD">Consolidated</in-capmkt:NatureOfReportStandaloneConsolidated>
  <in-capmkt:NameOfTheCompany contextRef="OneD">Test Manufacturing Co</in-capmkt:NameOfTheCompany>
  <in-capmkt:RevenueFromOperations contextRef="OneD">100000000000</in-capmkt:RevenueFromOperations>
  <in-capmkt:ProfitBeforeTax contextRef="OneD">20000000000</in-capmkt:ProfitBeforeTax>
  <in-capmkt:FinanceCosts contextRef="OneD">1000000000</in-capmkt:FinanceCosts>
  <in-capmkt:DepreciationDepletionAndAmortisationExpense contextRef="OneD">500000000</in-capmkt:DepreciationDepletionAndAmortisationExpense>
  <in-capmkt:ProfitLossForThePeriod contextRef="OneD">15000000000</in-capmkt:ProfitLossForThePeriod>
  <in-capmkt:BasicEarningsLossPerShare contextRef="OneD">10.5</in-capmkt:BasicEarningsLossPerShare>
</xbrli:xbrl>
"""

BANK_XBRL = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:in-gaap="http://www.sebi.gov.in/xbrl/in-gaap/2020-03-31/in-gaap">
  <xbrli:context id="OneD">
    <xbrli:entity><xbrli:identifier scheme="NSE">TESTBANK</xbrli:identifier></xbrli:entity>
    <xbrli:period>
      <xbrli:startDate>2026-01-01</xbrli:startDate>
      <xbrli:endDate>2026-03-31</xbrli:endDate>
    </xbrli:period>
  </xbrli:context>
  <in-gaap:NatureOfReportStandaloneConsolidated contextRef="OneD">Consolidated</in-gaap:NatureOfReportStandaloneConsolidated>
  <in-gaap:InterestEarned contextRef="OneD">50000000000</in-gaap:InterestEarned>
  <in-gaap:InterestExpended contextRef="OneD">30000000000</in-gaap:InterestExpended>
  <in-gaap:OperatingProfitBeforeProvisionsAndContingencies contextRef="OneD">8000000000</in-gaap:OperatingProfitBeforeProvisionsAndContingencies>
  <in-gaap:ProfitLossFromOrdinaryActivitiesBeforeTax contextRef="OneD">7000000000</in-gaap:ProfitLossFromOrdinaryActivitiesBeforeTax>
  <in-gaap:BasicEarningsPerShareBeforeExtraordinaryItems contextRef="OneD">8.25</in-gaap:BasicEarningsPerShareBeforeExtraordinaryItems>
</xbrli:xbrl>
"""
