#!/usr/bin/env python3
"""
Testing script para validar responsividad Phase 1
Verifica que los CSS media queries estén correctamente en los archivos
"""

import os
import re

def test_css_media_queries():
    """Test que CSS files tienen media queries correctas"""
    
    tests = {
        'home.css': {
            'file': 'static/css/home.css',
            'patterns': [
                (r'@media \(max-width: 576px\)', 'Media query 576px (Mobile)'),
                (r'@media \(max-width: 768px\)', 'Media query 768px (Tablet)'),
                (r'@media \(max-width: 992px\)', 'Media query 992px (Large)'),
                (r'\.navbar-home\s*{\s*padding:\s*15px 30px', 'Navbar padding flexible'),
                (r'\.logo-navbar\s*{\s*.*position:\s*relative', 'Logo relative positioning'),
                (r'\.brand-text\s*{\s*.*margin:\s*0', 'Brand text margin 0'),
            ]
        },
        'catalogo.css': {
            'file': 'static/css/catalogo.css',
            'patterns': [
                (r'@media \(max-width: 576px\)', 'Media query 576px (Mobile)'),
                (r'@media \(max-width: 768px\)', 'Media query 768px (Tablet)'),
                (r'\.main-content\s*{\s*margin-left:\s*240px', 'Main content desktop margin'),
                (r'\.main-content\s*{\s*margin-left:\s*0', 'Main content mobile margin 0'),
            ]
        },
        'login.css': {
            'file': 'static/css/login.css',
            'patterns': [
                (r'@media \(max-width: 576px\)', 'Media query 576px (Mobile)'),
                (r'\.login-card\s*{.*max-width:\s*520px', 'Login card max-width'),
            ]
        }
    }
    
    print("=" * 60)
    print("TESTING RESPONSIVITY PHASE 1")
    print("=" * 60)
    
    total_tests = 0
    passed_tests = 0
    
    for css_name, test_config in tests.items():
        filepath = test_config['file']
        print(f"\n📄 {css_name}")
        print("-" * 60)
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            for pattern, description in test_config['patterns']:
                total_tests += 1
                if re.search(pattern, content, re.DOTALL | re.IGNORECASE):
                    print(f"  ✅ {description}")
                    passed_tests += 1
                else:
                    print(f"  ❌ {description}")
        except FileNotFoundError:
            print(f"  ❌ File not found: {filepath}")
    
    print("\n" + "=" * 60)
    print(f"RESULTADOS: {passed_tests}/{total_tests} tests pasados")
    print("=" * 60)
    
    if passed_tests == total_tests:
        print("✅ PHASE 1 READY FOR BROWSER TESTING")
        return True
    else:
        print("❌ Some tests failed - review CSS files")
        return False

def test_html_viewport():
    """Test que HTML files tienen viewport meta tag"""
    
    print("\n\n" + "=" * 60)
    print("TESTING HTML VIEWPORT")
    print("=" * 60)
    
    html_files = [
        'templates/home.html',
        'templates/productos/catalogo.html',
        'templates/usuarios/login.html'
    ]
    
    viewport_pattern = r'<meta\s+name="viewport"'
    
    for html_file in html_files:
        print(f"\n📄 {html_file}")
        try:
            with open(html_file, 'r', encoding='utf-8') as f:
                content = f.read()
            if re.search(viewport_pattern, content):
                print(f"  ✅ Viewport meta tag presente")
            else:
                print(f"  ⚠️  Viewport meta tag NO encontrado")
        except FileNotFoundError:
            print(f"  ❌ File not found")

if __name__ == '__main__':
    test_css_media_queries()
    test_html_viewport()
    print("\n✅ Auto-validation complete. Ready for manual browser testing.")
