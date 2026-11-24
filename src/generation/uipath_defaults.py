"""
Shared UiPath XAML defaults: xmlns declarations and TextExpression imports for Studio-compatible output.
Used by the generator prompt, spec compiler, and optionally post-processing.
"""

from __future__ import annotations

# Key/value pairs for Activity root xmlns — match typical Studio / excel_processing.xaml style.
UIPATH_ACTIVITY_XMLNS: dict[str, str] = {
    "_default": "http://schemas.microsoft.com/netfx/2009/xaml/activities",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "mva": "clr-namespace:Microsoft.VisualBasic.Activities;assembly=System.Activities",
    "sap": "http://schemas.microsoft.com/netfx/2009/xaml/activities/presentation",
    "sap2010": "http://schemas.microsoft.com/netfx/2010/xaml/activities/presentation",
    "sap2016": "http://schemas.microsoft.com/netfx/2016/xaml/activities/presentation",
    "sads": "http://schemas.microsoft.com/netfx/2010/xaml/activities/debugger",
    "s": "clr-namespace:System;assembly=System.Private.CoreLib",
    "scg": "clr-namespace:System.Collections.Generic;assembly=System.Private.CoreLib",
    "sco": "clr-namespace:System.Collections.ObjectModel;assembly=System.Private.CoreLib",
    "sd": "clr-namespace:System.Data;assembly=System.Data.Common",
    "sg": "clr-namespace:System.Globalization;assembly=System.Private.CoreLib",
    "ss": "clr-namespace:System.Security;assembly=System.Private.CoreLib",
    "ue": "clr-namespace:UiPath.Excel;assembly=UiPath.Excel.Activities",
    "ueab": "clr-namespace:UiPath.Excel.Activities.Business;assembly=UiPath.Excel.Activities",
    "ui": "http://schemas.uipath.com/workflow/activities",
    "uix": "http://schemas.uipath.com/workflow/activities/uix",
    "x": "http://schemas.microsoft.com/winfx/2006/xaml",
}

# Order matters for readability (default xmlns first implied by parser, we emit explicit xmlns=).
ORDERED_XMLNS_KEYS = [
    "mc",
    "mva",
    "sap",
    "sap2010",
    "sap2016",
    "sads",
    "s",
    "scg",
    "sco",
    "sd",
    "sg",
    "ss",
    "ue",
    "ueab",
    "ui",
    "uix",
    "x",
]


def xmlns_lines_for_activity_multiline(indent: str = " ") -> str:
    """Multiline xmlns block for XAML Activity (after opening tag attrs x:Class / mc:Ignorable)."""
    lines = []
    lines.append(f'{indent}xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"')
    for key in ORDERED_XMLNS_KEYS:
        uri = UIPATH_ACTIVITY_XMLNS[key]
        lines.append(f'{indent}xmlns:{key}="{uri}"')
    return "\n".join(lines)


# VB expression imports — subset aligned with config/templates/excel_processing.xaml + common packages.
TEXT_EXPRESSION_NAMESPACE_STRINGS: tuple[str, ...] = (
    "System.Activities",
    "System.Activities.Statements",
    "System.Activities.Expressions",
    "System.Activities.Validation",
    "System.Activities.XamlIntegration",
    "Microsoft.VisualBasic",
    "Microsoft.VisualBasic.Activities",
    "System",
    "System.Collections",
    "System.Collections.Generic",
    "System.Collections.ObjectModel",
    "System.Data",
    "System.Diagnostics",
    "System.Drawing",
    "System.IO",
    "System.Linq",
    "System.Net.Mail",
    "System.Xml",
    "System.Xml.Linq",
    "System.Security",
    "System.Reflection",
    "System.ComponentModel",
    "System.Globalization",
    "UiPath.Core",
    "UiPath.Core.Activities",
    "UiPath.Core.Activities.SAP",
    "UiPath.Core.Activities.Orchestrator",
    "UiPath.Core.OCR",
    "UiPath.Excel",
    "UiPath.Excel.Activities",
    "UiPath.Excel.Activities.Business",
    "UiPath.Mail",
    "UiPath.Mail.Activities",
    "UiPath.Mail.Activities.Business",
    "UiPath.Mail.Outlook.Activities",
    "UiPath.Database.Activities",
    "UiPath.Database",
    "UiPath.PDF.Activities",
    "UiPath.WebAPI.Activities",
    "UiPath.UIAutomationCore.Contracts",
    "UiPath.UIAutomationNext.Activities",
    "UiPath.UIAutomationNext.Enums",
    "UiPath.UIAutomationNext.Models",
    "UiPath.UIAutomationNext.Contracts",
    "UiPath.Shared.Activities",
    "UiPath.Platform.ObjectLibrary",
    "UiPath.CV",
    "UiPath.CV.Activities",
    "UiPath.SAP",
    "UiPath.Shared.Activities.Business",
    "UiPath.DataTableUtilities",
)


def text_expression_namespaces_xml(indent_body: str = "    ") -> str:
    """TextExpression.NamespacesForImplementation block (inside Activity, after x:Members if any)."""
    rows = "\n".join(
        f'{indent_body}<x:String>{ns}</x:String>' for ns in TEXT_EXPRESSION_NAMESPACE_STRINGS
    )
    return (
        f"  <TextExpression.NamespacesForImplementation>\n"
        f"    <sco:Collection x:TypeArguments=\"x:String\">\n"
        f"{rows}\n"
        f"    </sco:Collection>\n"
        f"  </TextExpression.NamespacesForImplementation>\n"
    )


TEXT_EXPRESSION_REFERENCE_ASSEMBLIES: tuple[str, ...] = (
    "System.Activities",
    "Microsoft.VisualBasic",
    "System.Private.CoreLib",
    "System.Data",
    "System.Data.Common",
    "System.ServiceModel",
    "System.Xml",
    "System.Xml.Linq",
    "System.Drawing",
    "System.Core",
    "System.Xaml",
    "System.ComponentModel.TypeConverter",
    "WindowsBase",
    "System.Collections",
    "System.Collections.NonGeneric",
    "System.Linq",
    "System.Linq.Expressions",
    "System.Memory",
    "UiPath.System.Activities",
    "UiPath.UiAutomation.Activities",
    "UiPath.Excel.Activities",
    "UiPath.Mail.Activities",
    "UiPath.WebAPI.Activities",
    "UiPath.Database.Activities",
    "UiPath.PDF.Activities",
    "UiPath.Orchestrator.Activities",
    "UiPath.SAP.Activities",
)


def text_expression_references_xml(indent_body: str = "    ") -> str:
    rows = "\n".join(
        f"{indent_body}<AssemblyReference>{asm}</AssemblyReference>"
        for asm in TEXT_EXPRESSION_REFERENCE_ASSEMBLIES
    )
    return (
        f"  <TextExpression.ReferencesForImplementation>\n"
        f"    <sco:Collection x:TypeArguments=\"AssemblyReference\">\n"
        f"{rows}\n"
        f"    </sco:Collection>\n"
        f"  </TextExpression.ReferencesForImplementation>\n"
    )
